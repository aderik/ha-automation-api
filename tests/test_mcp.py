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
from unittest.mock import AsyncMock, MagicMock, patch

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


class HomeAssistantError(Exception):
    pass


sys.meta_path.insert(0, _StubFinder())
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import homeassistant.components.http  # noqa: E402
import homeassistant.exceptions  # noqa: E402
import homeassistant.helpers.json  # noqa: E402

homeassistant.components.http.HomeAssistantView = FakeView
homeassistant.exceptions.Unauthorized = Unauthorized
homeassistant.exceptions.HomeAssistantError = HomeAssistantError
homeassistant.helpers.json.json_dumps = json.dumps

from custom_components.automation_api import mcp  # noqa: E402


def rpc(method, params=None, *, admin=True, notification=False):
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notification:
        msg["id"] = 1
    request = MagicMock()
    request.json = AsyncMock(return_value=msg)
    request.__getitem__.return_value = SimpleNamespace(is_admin=admin, id="user-1")
    request.headers = {"Authorization": "Bearer t0ken"}
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
    assert len(tools) == 72, len(tools)
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

    # HACS / Marketplace: a WebSocket client of the store, as the caller
    from contextlib import asynccontextmanager

    from custom_components.automation_api import hacs

    def row(full_name, installed=False, stars=1, last_updated=None, category="integration"):
        return {
            "id": str(len(rows) + 100), "full_name": full_name, "name": full_name,
            "category": category, "domain": "x", "description": "",
            "installed": installed, "installed_version": None,
            "available_version": "v1", "pending_upgrade": False, "stars": stars,
            "downloads": 0, "last_updated": last_updated, "topics": [],
        }

    rows, sent, tokens, fail = [], [], [], {}
    renamed = {"old/name": "new/name"}

    @asynccontextmanager
    async def fake_connect(hass, token):
        """A store that answers its websocket commands from `rows`."""
        tokens.append(token)

        async def command(type_, **fields):
            sent.append((type_, fields))
            if type_ in fail:
                raise hacs.HacsError(fail[type_])
            command = type_.split("/", 1)[1]
            if command == "repositories/list":
                wanted = fields.get("categories")
                return [dict(r) for r in rows if not wanted or r["category"] in wanted]
            if command == "repositories/add":
                if fields["category"] == "integration":  # anything else fails validation
                    name = fields["repository"]
                    rows.append(row(renamed.get(name, name)))
                return None
            target = next(r for r in rows if r["id"] == fields["repository"])
            target["installed"] = command in ("repository/install", "repository/download")
            return None

        yield command

    hacs._connect = fake_connect
    entry = SimpleNamespace(options={})
    set_up = {"hacs"}
    fake_hass = SimpleNamespace(
        data={"automation_api": {"entry": entry}},
        config_entries=SimpleNamespace(async_entries=lambda d: [1] if d in set_up else []),
    )

    def run(coro):
        try:
            return asyncio.run(coro)
        except hacs.HacsError as e:
            return e

    # downloads are opt-in, and the store is asked with the caller's token
    assert "disabled" in str(run(hacs.download(fake_hass, "t", "a/b")))
    entry.options["allow_hacs"] = True
    assert "access token" in str(run(hacs.download(fake_hass, None, "a/b")))
    assert not sent, sent

    # HACS: unknown repos are added as custom first
    rows.append(row("e/f", category="plugin"))
    assert "pass `category` (one of ['plugin'])" in str(run(hacs.download(fake_hass, "t", "a/b")))
    assert "validation failed" in str(run(hacs.download(fake_hass, "t", "a/b", category="x")))
    sent.clear()
    result = run(hacs.download(fake_hass, "t", "https://github.com/a/b/", category="integration"))
    assert result["full_name"] == "a/b" and result["installed"] and result["restart_required"], result
    assert "topics" not in result, result
    a_b = result["id"]
    assert [m for m in sent if "list" not in m[0]] == [
        ("hacs/repositories/add", {"repository": "a/b", "category": "integration"}),
        ("hacs/repository/download", {"repository": a_b}),
    ], sent
    result = run(hacs.download(fake_hass, "t", "old/name", category="integration"))
    assert result["full_name"] == "new/name" and result["installed"], result

    # remove: only installed repos, never HACS itself; gated like downloads
    result = run(hacs.remove(fake_hass, "t", "A/B"))
    assert result["restart_required"] and not result["installed"], result
    assert sent[-2] == ("hacs/repository/remove", {"repository": a_b}), sent[-2]
    assert "not installed" in str(run(hacs.remove(fake_hass, "t", "x/y")))
    assert "not installed" in str(run(hacs.remove(fake_hass, "t", "a/b")))
    rows.append(row("hacs/integration", installed=True))
    assert "refusing" in str(run(hacs.remove(fake_hass, "t", "hacs/integration")))
    entry.options["allow_hacs"] = False
    assert "disabled" in str(run(hacs.remove(fake_hass, "t", "new/name")))
    entry.options["allow_hacs"] = True

    names = lambda **kw: [r["full_name"] for r in run(hacs.search(fake_hass, "t", **kw))]
    assert names(query="E/F") == ["e/f"]
    assert names(category="plugin") == ["e/f"]
    assert sent[-1] == ("hacs/repositories/list", {"categories": ["plugin"]}), sent[-1]
    assert set(names(installed=True)) == {"new/name", "hacs/integration"}
    rows[:] = []
    rows.append(row("b/old", stars=9, last_updated="2025-01-01T00:00:00Z"))
    rows.append(row("a/new", stars=1, last_updated="2026-09-01T00:00:00Z"))
    rows.append(row("c/none", stars=5))
    assert names() == ["b/old", "c/none", "a/new"]
    assert names(sort="last_updated") == ["a/new", "b/old", "c/none"]
    assert names(sort="name") == ["a/new", "b/old", "c/none"]
    assert names(limit=1) == ["b/old"]
    assert "invalid sort" in str(run(hacs.search(fake_hass, "t", sort="bogus")))

    # Marketplace (2026.11+): preferred over HACS; its refusals are passed on
    set_up.add("marketplace")
    rows.append(row("M/p"))
    m_p = rows[-1]["id"]
    result = run(hacs.download(fake_hass, "t", m_p, version="v2"))
    assert result["full_name"] == "M/p" and result["installed"], result
    assert sent[-2] == (
        "marketplace/repository/install",
        {"repository": m_p, "version": "v2"},
    ), sent[-2]
    fail["marketplace/repository/uninstall"] = "m/p is in use (repository_in_use)"
    assert "in use" in str(run(hacs.remove(fake_hass, "t", "m/p")))
    fail["marketplace/repositories/list"] = "not loaded (not_loaded)"
    assert "not_loaded" in str(run(hacs.search(fake_hass, "t")))
    fail.clear()
    set_up.clear()
    assert "neither" in str(run(hacs.search(fake_hass, "t")))

    # the views and tools hand the store the bearer token of the request
    is_error, text = call("hacs_search", query="M/P")
    assert not is_error and json.loads(text)["items"][0]["full_name"] == "M/p", text
    assert tokens[-1] == "t0ken", tokens[-1]

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

    # matter commission: manual code loses its dashes, needs a loaded Matter
    import homeassistant.components.matter.helpers as matter_helpers

    client = MagicMock()
    client.commission_with_code = AsyncMock(return_value=SimpleNamespace(node_id=12))
    matter_helpers.get_matter = lambda h: SimpleNamespace(matter_client=client)
    fake_ce.async_loaded_entries.return_value = ["matter-entry"]
    assert asyncio.run(registry.commission_matter(ha, " 1234-567 8901 ")) == {
        "status": "ok", "node_id": 12,
    }
    client.commission_with_code.assert_awaited_with("12345678901", network_only=True)
    asyncio.run(registry.commission_matter(ha, "MT:Y.K90-Q000KA0648G00", False))
    client.commission_with_code.assert_awaited_with("MT:Y.K90-Q000KA0648G00", network_only=False)
    fake_ce.async_loaded_entries.return_value = []
    try:
        asyncio.run(registry.commission_matter(ha, "12345678901"))
    except ValueError:
        pass
    else:
        raise AssertionError("commissioned without Matter")
    is_error, text = call("commission_matter", code=" ")
    assert is_error and "missing code" in text

    # device iteration: old mapping registry and the 2026.x entry view
    assert list(registry._iter_devices(SimpleNamespace(devices={"d1": "dev1"}))) == ["dev1"]
    assert list(registry._iter_devices(SimpleNamespace(devices=("dev1",)))) == ["dev1"]

    # repairs: active issues only, title/description translated and filled in
    from datetime import datetime

    issue = SimpleNamespace(
        domain="sensor", issue_id="units_changed_x", translation_key="units_changed",
        translation_placeholders={"statistic_id": "sensor.x"}, active=True,
        severity=SimpleNamespace(value="warning"), breaks_in_ha_version=None,
        is_fixable=False, learn_more_url=None, created=datetime(2026, 11, 13), data=None,
    )
    gone = SimpleNamespace(**{**vars(issue), "issue_id": "old", "active": False})
    mcp.views.issue_registry.async_get = lambda h: SimpleNamespace(issues={1: issue, 2: gone})
    mcp.views.translation.async_get_translations = AsyncMock(return_value={
        "component.sensor.issues.units_changed.title": "Unit of {statistic_id} changed",
        "component.sensor.issues.units_changed.description": "Bad {missing}",
    })
    is_error, text = call("list_repairs")
    (item,) = json.loads(text)["items"]
    assert not is_error and item["issue_id"] == "units_changed_x"
    assert item["title"] == "Unit of sensor.x changed"
    assert item["description"] == "Bad {missing}"  # unknown placeholder: raw text
    assert item["severity"] == "warning"

    # notifications
    mcp.views._async_get_or_create_notifications = lambda h: {
        "n1": {"notification_id": "n1", "title": None, "message": "Login failed",
               "created_at": datetime(2026, 11, 13)},
    }
    is_error, text = call("list_notifications")
    assert json.loads(text)["items"][0]["message"] == "Login failed"

    # statistics metadata: same call as the Repairs fix, waits for the recorder
    import homeassistant.components.recorder.statistics as rec_stats
    import homeassistant.helpers.typing as ha_typing

    seen = {}

    def fake_update(hass, statistic_id, **kw):
        seen.update(kw, statistic_id=statistic_id)
        hass.loop.call_soon_threadsafe = lambda f: f()
        kw["on_done"]()

    rec_stats.async_update_statistics_metadata = fake_update
    is_error, text = call(
        "update_statistic_unit", statistic_id="sensor.x",
        unit_of_measurement="kWh", unit_class="energy",
    )
    assert not is_error and json.loads(text)["status"] == "ok"
    assert (seen["statistic_id"], seen["new_unit_of_measurement"], seen["new_unit_class"]) == (
        "sensor.x", "kWh", "energy",
    )
    call("update_statistic_unit", statistic_id="sensor.x", unit_of_measurement="kWh")
    assert seen["new_unit_class"] is ha_typing.UNDEFINED

    # energy prefs: only the passed lists change, after HA's schema check
    import probatio
    import homeassistant.components.energy.data as energy_data

    class Invalid(Exception):
        pass

    probatio.Invalid = Invalid

    def schema(spec):
        def check(value):
            for source in value.get("energy_sources", []):
                if source.get("type") not in ("grid", "solar"):
                    raise Invalid("expected type to be one of grid, solar")
            return value
        return check

    probatio.Schema = schema
    manager = MagicMock(data={"energy_sources": [], "device_consumption": []})

    async def energy_update(update):
        manager.data = {**manager.data, **update}

    manager.async_update = AsyncMock(side_effect=energy_update)
    energy_data.async_get_manager = AsyncMock(return_value=manager)
    from custom_components.automation_api import http as views_mod

    h = views_mod._energy_hash(manager.data)
    device = [{"stat_consumption": "sensor.d"}]
    is_error, text = call("set_energy_prefs", config_hash=h, device_consumption=device, dry_run=True)
    assert not is_error and json.loads(text)["changed"] == ["device_consumption"], text
    manager.async_update.assert_not_awaited()
    is_error, text = call("set_energy_prefs", config_hash=h, device_consumption=device)
    assert not is_error, text
    manager.async_update.assert_awaited_with({"device_consumption": device})
    is_error, text = call("set_energy_prefs", config_hash=h, device_consumption=[])
    assert is_error and "changed since" in text, text  # h is stale now
    assert manager.async_update.await_count == 1
    is_error, text = call("set_energy_prefs", config_hash=h, energy_sources=[{"type": "bogus"}])
    assert is_error and "expected type" in text, text
    is_error, text = call("set_energy_prefs", config_hash=h)
    assert is_error and "pass one or more" in text, text

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
