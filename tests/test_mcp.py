"""Self-check for the MCP endpoint. Runs without Home Assistant installed:

    python3 tests/test_mcp.py

Home Assistant, aiohttp and voluptuous are replaced by mocks, so this covers
the JSON-RPC layer, the generated tool schemas and the in-process dispatch to
the REST views — not Home Assistant itself.
"""

import asyncio
import importlib.abc
import importlib.machinery
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

STUBBED = ("homeassistant", "aiohttp", "voluptuous", "probatio")


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in STUBBED:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        return MagicMock(name=spec.name)

    def exec_module(self, module):
        pass


class FakeView:
    def json(self, result, status_code=200):
        return SimpleNamespace(body=json.dumps(result).encode(), status=status_code)


class Unauthorized(Exception):
    pass


sys.meta_path.insert(0, _StubFinder())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import homeassistant.components.http  # noqa: E402
import homeassistant.exceptions  # noqa: E402
import homeassistant.helpers.json  # noqa: E402

homeassistant.components.http.HomeAssistantView = FakeView
homeassistant.exceptions.Unauthorized = Unauthorized
homeassistant.helpers.json.json_dumps = json.dumps

from custom_components.automation_api import mcp  # noqa: E402


def rpc(method, params=None, *, admin=True, notification=False):
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notification:
        msg["id"] = 1
    request = MagicMock()
    request.json = AsyncMock(return_value=msg)
    request.__getitem__.return_value = SimpleNamespace(is_admin=admin, id="user-1")
    resp = asyncio.run(mcp.McpView().post(request))
    return json.loads(resp.body) if isinstance(resp, SimpleNamespace) else resp


def call(name, **arguments):
    result = rpc("tools/call", {"name": name, "arguments": arguments})["result"]
    return result["isError"], result["content"][0]["text"]


def main():
    # initialize: echo a supported protocol version, fall back otherwise
    init = rpc("initialize", {"protocolVersion": "2025-03-26"})["result"]
    assert init["protocolVersion"] == "2025-03-26"
    assert init["capabilities"] == {"tools": {}}
    assert (
        rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"]
        == mcp.DEFAULT_PROTOCOL_VERSION
    )

    # notifications get 202 and no body
    rpc("notifications/initialized", notification=True)
    mcp.web.Response.assert_called_with(status=202)

    # tools/list: all tools, schemas derived from the signatures
    tools = {t["name"]: t for t in rpc("tools/list")["result"]["tools"]}
    assert len(tools) == 62, len(tools)
    schema = tools["create_dashboard"]["inputSchema"]
    assert schema["required"] == ["url_path", "title"]
    assert schema["properties"]["show_in_sidebar"] == {"type": "boolean"}
    assert schema["properties"]["icon"] == {"type": "string"}
    schema = tools["replace_dashboard_view"]["inputSchema"]
    assert schema["properties"]["view_index"] == {"type": "integer"}
    assert schema["properties"]["view"] == {"type": "object"}
    assert tools["upsert_notify_group"]["inputSchema"]["properties"]["services"] == {
        "type": "array"
    }
    assert tools["list_areas"]["description"].startswith("List every area")

    # tools/call goes through the REST view: path args, JSON result
    mcp.views.package.list_helpers = AsyncMock(return_value=[{"id": "x"}])
    assert call("list_helpers", domain="input_boolean") == (
        False,
        json.dumps({"items": [{"id": "x"}]}),
    )

    # a 4xx from the view becomes a tool error
    is_error, text = call("list_helpers", domain="bogus")
    assert is_error and "HTTP 400" in text and "unsupported domain" in text

    # request body reaches the view; path + body both passed
    mcp.views.package.upsert_helper = AsyncMock()
    mcp.views.package.reload_domain = AsyncMock(return_value=True)
    mcp.views.log = AsyncMock()
    is_error, text = call(
        "upsert_helper", domain="input_boolean", helper_id="x", config={"name": "X"}
    )
    assert not is_error, text
    assert mcp.views.package.upsert_helper.await_args.args[1:] == (
        "input_boolean",
        "x",
        {"name": "X"},
    )

    # query args: None dropped, booleans survive the string round-trip
    mcp.views.registry_mod.list_entities = AsyncMock(return_value=[])
    assert call("list_registry_entities", platform="tuya", disabled=False)[0] is False
    kwargs = mcp.views.registry_mod.list_entities.await_args.kwargs
    assert kwargs["platform"] == "tuya"
    assert kwargs["disabled"] is False
    assert kwargs["domain"] is None

    # config flows: body reaches the view, missing domain is rejected
    mcp.views.registry_mod.start_config_flow = AsyncMock(
        return_value={"type": "form", "flow_id": "f1"}
    )
    assert call("start_config_flow", domain="met") == (
        False,
        json.dumps({"type": "form", "flow_id": "f1"}),
    )
    assert mcp.views.registry_mod.start_config_flow.await_args.args[1] == "met"
    is_error, text = call("start_config_flow", domain="")
    assert is_error and "missing domain" in text

    # flow results are made JSON-safe: schema serialised, entry -> entry_id
    from custom_components.automation_api import registry
    import probatio

    probatio.to_field_list = MagicMock(return_value=[{"name": "host"}])
    form = registry._flow_result_to_dict(
        {"type": "form", "data_schema": object(), "context": {"source": "user"}}
    )
    assert form == {"type": "form", "data_schema": [{"name": "host"}]}, form
    assert registry._flow_result_to_dict({"type": "menu", "data_schema": None})[
        "data_schema"
    ] == []
    done = registry._flow_result_to_dict(
        {
            "type": "create_entry",
            "result": SimpleNamespace(entry_id="e1"),
            "data": {"password": "x"},
            "title": "Met",
        }
    )
    assert done == {"type": "create_entry", "entry_id": "e1", "title": "Met"}, done

    # bad arguments are a tool error, not a transport error
    is_error, text = call("get_helper", nope=1)
    assert is_error and "TypeError" in text

    # protocol errors
    assert rpc("tools/call", {"name": "nope"})["error"]["code"] == -32602
    assert rpc("resources/list")["error"]["code"] == -32601

    # admin only
    try:
        rpc("tools/list", admin=False)
    except Unauthorized:
        pass
    else:
        raise AssertionError("non-admin was not rejected")

    print("ok")


if __name__ == "__main__":
    main()
