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
    assert len(tools) == 66, len(tools)
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

    # HACS: downloads are opt-in; unknown repos are added as custom first
    from custom_components.automation_api import hacs

    sys.modules["custom_components.hacs"] = MagicMock()
    sys.modules["custom_components.hacs.enums"] = MagicMock()

    def fake_repo(full_name, installed=False, stars=1, last_updated=None):
        return SimpleNamespace(
            data=SimpleNamespace(
                id="1", full_name=full_name, category="integration", domain="x",
                description="", installed=installed, stargazers_count=stars,
                downloads=0, last_fetched=1, last_updated=last_updated,
            ),
            display_name=full_name, display_installed_version=None,
            display_available_version="v1", pending_update=False,
            ignored_by_country_configuration=False,
            async_download_repository=AsyncMock(),
        )

    store = {}
    hacs_base = MagicMock()
    hacs_base.system.disabled = False
    hacs_base.common.categories = {"integration", "plugin"}
    hacs_base.common.skip = set()
    hacs_base.repositories.get_by_id.return_value = None
    hacs_base.repositories.get_by_full_name.side_effect = store.get
    hacs_base.async_recreate_entities = AsyncMock()
    hacs_base.data.async_write = AsyncMock()

    async def register(repository_full_name, category):
        store[repository_full_name] = fake_repo(repository_full_name)

    hacs_base.async_register_repository = AsyncMock(side_effect=register)
    entry = SimpleNamespace(options={})
    fake_hass = SimpleNamespace(data={"hacs": hacs_base, "automation_api": {"entry": entry}})

    def run(coro):
        try:
            return asyncio.run(coro)
        except hacs.HacsError as e:
            return e

    assert "disabled" in str(run(hacs.download(fake_hass, "a/b")))
    entry.options["allow_hacs"] = True
    assert "pass `category`" in str(run(hacs.download(fake_hass, "a/b")))
    assert "invalid category" in str(run(hacs.download(fake_hass, "a/b", category="x")))
    result = run(hacs.download(fake_hass, "https://github.com/a/b/", category="integration"))
    assert result["full_name"] == "a/b" and result["restart_required"], result
    store["a/b"].async_download_repository.assert_awaited_with(ref=None)
    hacs_base.async_recreate_entities.assert_awaited()
    hacs_base.async_register_repository.side_effect = None  # validation fails
    assert "validation failed" in str(run(hacs.download(fake_hass, "c/d", category="plugin")))

    # remove: only installed repos, never HACS itself; gated like downloads
    store["a/b"].data.installed = True
    store["a/b"].update_repository = AsyncMock(side_effect=RuntimeError("offline"))
    store["a/b"].uninstall = AsyncMock()
    result = run(hacs.remove(fake_hass, "a/b"))
    assert result["restart_required"], result
    store["a/b"].uninstall.assert_awaited_once()
    assert "not installed" in str(run(hacs.remove(fake_hass, "x/y")))
    store["hacs/integration"] = fake_repo("hacs/integration", installed=True)
    assert "refusing" in str(run(hacs.remove(fake_hass, "hacs/integration")))
    entry.options["allow_hacs"] = False
    assert "disabled" in str(run(hacs.remove(fake_hass, "a/b")))
    entry.options["allow_hacs"] = True

    hacs_base.repositories.list_all = [fake_repo("a/b", installed=True), fake_repo("e/f")]
    assert [r["full_name"] for r in run(hacs.search(fake_hass, query="E/F"))] == ["e/f"]
    assert len(run(hacs.search(fake_hass, installed=True))) == 1
    hacs_base.repositories.list_all = [
        fake_repo("b/old", stars=9, last_updated="2025-01-01T00:00:00Z"),
        fake_repo("a/new", stars=1, last_updated="2026-09-01T00:00:00Z"),
        fake_repo("c/none", stars=5),
    ]
    names = lambda **kw: [r["full_name"] for r in run(hacs.search(fake_hass, **kw))]
    assert names() == ["b/old", "c/none", "a/new"]
    assert names(sort="last_updated") == ["a/new", "b/old", "c/none"]
    assert names(sort="name") == ["a/new", "b/old", "c/none"]
    assert "invalid sort" in str(run(hacs.search(fake_hass, sort="bogus")))

    # config entry host: only the address is exposed, and only it is changed
    entry_obj = SimpleNamespace(
        entry_id="e1", domain="twinkly", title="T", source="user",
        state="setup_retry", disabled_by=None,
        data={"host": "10.0.0.1", "token": "secret"},
    )
    fake_ce = MagicMock()
    fake_ce.async_get_entry.return_value = entry_obj
    fake_ce.async_reload = AsyncMock()
    def update_entry(entry, data):
        entry.data = data
    fake_ce.async_update_entry.side_effect = update_entry
    ha = SimpleNamespace(config_entries=fake_ce)
    item = asyncio.run(registry.set_config_entry_host(ha, "e1", "10.0.0.2"))
    assert item["host"] == "10.0.0.2" and "secret" not in json.dumps(item), item
    assert entry_obj.data == {"host": "10.0.0.2", "token": "secret"}
    fake_ce.async_reload.assert_awaited_with("e1")
    entry_obj.data = {"token": "x"}
    try:
        asyncio.run(registry.set_config_entry_host(ha, "e1", "10.0.0.3"))
    except ValueError:
        pass
    else:
        raise AssertionError("entry without host was changed")

    # device iteration: old mapping registry and the 2026.x entry view
    assert list(registry._iter_devices(SimpleNamespace(devices={"d1": "dev1"}))) == ["dev1"]
    assert list(registry._iter_devices(SimpleNamespace(devices=("dev1",)))) == ["dev1"]

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
