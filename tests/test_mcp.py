"""Self-check for the MCP endpoint. Runs without Home Assistant installed:

    python3 tests/test_mcp.py

Home Assistant, aiohttp and voluptuous are replaced by mocks, so this covers
the JSON-RPC layer, the generated tool schemas and the in-process dispatch to
the REST views — not Home Assistant itself.
"""

import asyncio
from contextlib import asynccontextmanager
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


HASS = MagicMock()  # the hass every request gets; tests hang fakes on it


def rpc(method, params=None, *, admin=True, notification=False):
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notification:
        msg["id"] = 1
    request = MagicMock()
    request.app = {"hass": HASS}
    request.json = AsyncMock(return_value=msg)
    request.__getitem__.return_value = SimpleNamespace(is_admin=admin, id="user-1")
    request.headers = {"Authorization": "Bearer t0ken"}
    resp = asyncio.run(mcp.McpView().post(request))
    return json.loads(resp.body) if isinstance(resp, SimpleNamespace) else resp


def call(tool, /, **arguments):
    result = rpc("tools/call", {"name": tool, "arguments": arguments})["result"]
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
    assert len(tools) == 80, len(tools)
    schema = tools["create_dashboard"]["inputSchema"]
    assert schema["required"] == ["url_path", "title"]
    assert schema["properties"]["show_in_sidebar"] == {"type": "boolean"}
    assert schema["properties"]["icon"] == {"type": "string"}
    schema = tools["replace_dashboard_view"]["inputSchema"]
    assert schema["properties"]["view_index"] == {"type": "integer"}
    assert schema["properties"]["view"] == {"type": "object"}
    schema = tools["upsert_helper"]["inputSchema"]
    assert schema["required"] == ["domain", "config"], schema
    assert schema["properties"]["helper_id"] == {"type": "string"}
    assert schema["properties"]["config"] == {"type": "object"}
    assert tools["list_entities"]["inputSchema"]["properties"]["domain"] == {"type": "string"}
    assert not {"get_managed_package", "upsert_notify_group"} & tools.keys()
    schema = tools["get_operation_status"]["inputSchema"]
    assert schema["required"] == ["operation_ids"] and schema["properties"] == {
        "operation_ids": {"type": "array"},
        "timeout_seconds": {"type": "number"},
    }, schema
    assert tools["list_areas"]["description"].startswith("List every area")

    mcp.views.log = AsyncMock()

    # input helpers are ordinary UI helpers: made, changed and removed through
    # the websocket commands of their integration, as the caller
    from custom_components.automation_api import helpers

    stored, helper_sent = {}, []

    @asynccontextmanager
    async def fake_helpers(hass, token, error):
        async def command(type_, **fields):
            helper_sent.append((token, type_, dict(fields)))
            domain, command = type_.split("/")
            items = stored.setdefault(domain, {})
            if command == "list":
                return list(items.values())
            if command == "create":
                if "name" not in fields:
                    raise error("required key not provided @ data['name'] (invalid_format)")
                item = {**fields, "id": fields["name"].lower().replace(" ", "_")}
            else:
                item = {**fields, "id": fields.pop(f"{domain}_id")}
                item.pop(f"{domain}_id")
                if command == "delete":
                    del items[item["id"]]
                    return None
            items[item["id"]] = item
            return item

        yield command

    helpers._connect = fake_helpers
    helpers.er = SimpleNamespace(
        async_get=lambda hass: SimpleNamespace(
            async_get_entity_id=lambda domain, platform, unique_id: f"{domain}.{unique_id}"
        )
    )

    # tools/call goes through the REST view: path args, body, JSON result
    is_error, text = call("upsert_helper", domain="input_boolean", config={"name": "Bell On"})
    assert not is_error, text
    bell = {"name": "Bell On", "id": "bell_on", "entity_id": "input_boolean.bell_on"}
    assert json.loads(text) == {"status": "ok", "domain": "input_boolean", "helper": bell}
    assert helper_sent[-1] == ("t0ken", "input_boolean/create", {"name": "Bell On"})
    assert call("list_helpers", domain="input_boolean") == (
        False,
        json.dumps({"items": {"bell_on": bell}}),
    )
    assert json.loads(call("get_helper", domain="input_boolean", helper_id="bell_on")[1]) == bell

    # a 4xx from the view becomes a tool error
    is_error, text = call("list_helpers", domain="bogus")
    assert is_error and "HTTP 400" in text and "unsupported helper domain" in text
    is_error, text = call("upsert_helper", domain="input_boolean", config={"icon": "mdi:bell"})
    assert is_error and "HTTP 400" in text and "required key" in text, text
    is_error, text = call("upsert_helper", domain="input_boolean", config={"name": "n", "id": "x"})
    assert is_error and "leave out 'id'" in text, text
    is_error, text = call("upsert_helper", domain="input_boolean", config={"name": "n", "type": "x"})
    assert is_error and "['type'] are not fields" in text, text

    # with an id the helper gets a full new config; one read back from the
    # list may be sent as it came
    is_error, text = call(
        "upsert_helper", domain="input_boolean", helper_id="bell_on",
        config={**bell, "name": "Bell", "icon": "mdi:bell"},
    )
    assert not is_error and json.loads(text)["helper"]["icon"] == "mdi:bell", text
    assert helper_sent[-1][1:] == (
        "input_boolean/update",
        {"input_boolean_id": "bell_on", "name": "Bell", "icon": "mdi:bell"},
    ), helper_sent[-1]
    is_error, text = call("upsert_helper", domain="input_boolean", helper_id="nope", config={"name": "n"})
    assert is_error and "HTTP 404" in text, text
    assert call("delete_helper", domain="input_boolean", helper_id="bell_on")[0] is False
    assert stored["input_boolean"] == {}
    is_error, text = call("delete_helper", domain="input_boolean", helper_id="bell_on")
    assert is_error and "HTTP 404" in text, text

    # automations and scripts are saved through the config API of their
    # editors, as the caller; this integration writes no YAML
    from custom_components.automation_api import config_api

    saved, requests = {"automation": {}, "script": {}, "scene": {}}, []

    async def fake_request(hass, token, error, method, path, body=None):
        requests.append((token, method, path, body))
        domain, key = path.split("/")[3], path.split("/")[5]
        items = saved[domain]
        if method == "POST":
            if body.get("mode") == "bogus":
                return 400, {"message": "Message malformed: bad mode"}
            items[key] = body
        elif key not in items:
            return (400 if method == "DELETE" else 404), {"message": "Resource not found"}
        elif method == "GET":
            return 200, items[key]
        else:
            del items[key]
        return 200, {"result": "ok"}

    config_api._request = fake_request
    HASS.services.async_call = AsyncMock()
    # voluptuous is a stub here: stand in for the defaults of the schema
    mcp.views.CREATE_SCHEMA = lambda data: {"description": "", "condition": [], "mode": "single", **data}

    is_error, text = call(
        "create_or_update_automation", id="a1", name="A",
        trigger=[{"trigger": "sun", "event": "sunset"}], action=[{"action": "light.turn_on"}],
    )
    assert not is_error and json.loads(text) == {"status": "ok", "id": "a1"}, text
    assert requests[-1] == (
        "t0ken",
        "POST",
        "/api/config/automation/config/a1",
        {
            "alias": "A", "description": "",
            "trigger": [{"trigger": "sun", "event": "sunset"}], "condition": [],
            "action": [{"action": "light.turn_on"}], "mode": "single",
        },
    ), requests[-1]
    # loaded on return: the config API itself only reloads in the background
    HASS.services.async_call.assert_awaited_with(
        "automation", "reload", {"id": "a1"}, blocking=True
    )
    is_error, text = call(
        "create_or_update_automation", id="a2", name="B", trigger=[], action=[], mode="bogus"
    )
    assert HASS.services.async_call.await_count == 1
    assert is_error and "Message malformed: bad mode (HTTP 400)" in text, text
    assert call("delete_automation", automation_id="a1")[0] is False and not saved["automation"]
    assert [r[1] for r in requests[-2:]] == ["GET", "DELETE"], requests[-2:]
    is_error, text = call("delete_automation", automation_id="a1")
    assert is_error and "HTTP 404" in text, text

    script = {"alias": "S", "sequence": [{"action": "light.turn_off"}]}
    is_error, text = call("create_or_update_script", script_id="s1", config=script)
    assert not is_error and json.loads(text)["entity_id"] == "script.s1", text
    assert requests[-1] == ("t0ken", "POST", "/api/config/script/config/s1", script)
    HASS.services.async_call.assert_awaited_with("script", "reload", {}, blocking=True)
    assert json.loads(call("get_script", script_id="s1")[1]) == script
    is_error, text = call("get_script", script_id="nope")
    assert is_error and "HTTP 404" in text, text
    assert call("delete_script", script_id="s1")[0] is False and not saved["script"]
    asyncio.run(config_api.get(HASS, "t", "script", "a/b"))
    assert requests[-1][2] == "/api/config/script/config/a%2Fb", requests[-1]

    # scenes go the same way; their entity id comes from the registry, since
    # Home Assistant names the entity after the scene and not after its id
    registry_stub = mcp.views.entity_registry
    mcp.views.entity_registry = SimpleNamespace(
        async_get=lambda hass: SimpleNamespace(
            async_get_entity_id=lambda domain, platform, unique_id: (
                "scene.movie_night" if (domain, platform, unique_id) == ("scene", "homeassistant", "m1") else None
            )
        )
    )
    scene = {"name": "Movie night", "entities": {"light.tv": {"state": "on", "brightness": 80}}}
    is_error, text = call("create_or_update_scene", scene_id="m1", config=scene)
    assert not is_error and json.loads(text) == {
        "status": "ok", "id": "m1", "entity_id": "scene.movie_night",
    }, text
    assert requests[-1] == ("t0ken", "POST", "/api/config/scene/config/m1", scene)
    HASS.services.async_call.assert_awaited_with("scene", "reload", {}, blocking=True)
    assert json.loads(call("get_scene", scene_id="m1")[1]) == scene
    assert call("delete_scene", scene_id="m1")[0] is False and not saved["scene"]
    is_error, text = call("delete_scene", scene_id="m1")
    assert is_error and "HTTP 404" in text, text
    mcp.views.entity_registry = registry_stub

    # template entities and history_stats sensors are config entries: made
    # with the config flow of their integration, changed with its options flow
    from custom_components.automation_api import helper_entries

    class Invalid(Exception):
        pass

    helper_entries.vol.Invalid = Invalid
    helper_entries.er = SimpleNamespace(
        async_get=lambda hass: None,
        async_entries_for_config_entry=lambda registry, entry_id: [
            SimpleNamespace(entity_id=f"sensor.of_{entry_id}")
        ],
    )

    class Marker:
        """A key of a schema, as voluptuous wraps it."""

        def __init__(self, schema):
            self.schema = schema

    def form(*fields, required=()):
        schema = SimpleNamespace(schema={Marker(f): None for f in fields})
        return {"type": "form", "data_schema": schema, "required": required}

    # domain -> the steps of its config flow and of its options flow
    FLOWS = {
        "template": (
            [
                {"type": "menu", "menu_options": ["binary_sensor", "sensor"]},
                form("name", "state", "unit_of_measurement", required=("name", "state")),
            ],
            [form("state", "unit_of_measurement", required=("state",))],
        ),
        "history_stats": (
            [
                form("name", "entity_id", "type", required=("entity_id",)),
                form("entity_id", "start", "end", "duration"),
            ],
            [form("entity_id", "start", "end", "duration")],
        ),
    }
    entries, aborted = {}, []

    class Flows:
        """A flow manager that walks the steps above."""

        def __init__(self, options):
            self.options = options

        def _show(self):
            if self.at == len(self.steps):
                if self.options:
                    self.entry.options = self.data
                else:
                    self.entry = SimpleNamespace(
                        entry_id=f"e{len(entries) + 1}", domain=self.domain,
                        title=self.data.get("name"), options=self.data,
                    )
                    entries[self.entry.entry_id] = self.entry
                return {"type": "create_entry", "result": self.entry}
            return {**self.steps[self.at], "flow_id": "f1", "errors": self.errors}

        async def async_init(self, handler, context):
            self.entry = entries.get(handler)
            self.domain = self.entry.domain if self.options else handler
            self.steps = FLOWS[self.domain][self.options]
            self.at, self.data, self.errors = 0, {}, None
            if self.options:
                # an options form replaces its own fields and keeps the rest
                asked = {marker.schema for marker in self.steps[0]["data_schema"].schema}
                self.data = {k: v for k, v in self.entry.options.items() if k not in asked}
            return self._show()

        async def async_configure(self, flow_id, answer):
            step = self.steps[self.at]
            if step["type"] == "menu":
                self.data["template_type"] = answer["next_step_id"]
            else:
                if missing := [f for f in step["required"] if f not in answer]:
                    raise Invalid(f"required key not provided: {missing}")
                if answer.get("state") == "bad":
                    self.errors = {"state": "invalid_template"}
                    return self._show()
                self.data.update(answer)
            self.at += 1
            return self._show()

        def async_abort(self, flow_id):
            aborted.append(flow_id)

    async def remove(entry_id):
        del entries[entry_id]

    HASS.config_entries = SimpleNamespace(
        flow=Flows(False),
        options=Flows(True),
        async_entries=lambda domain: [e for e in entries.values() if e.domain == domain],
        async_get_entry=entries.get,
        async_remove=remove,
    )

    is_error, text = call(
        "upsert_template_entity", template_type="sensor",
        config={"name": "Days", "state": "{{ 1 }}", "unit_of_measurement": "d"},
    )
    assert not is_error, text
    assert json.loads(text) == {
        "status": "ok", "entry_id": "e1", "title": "Days",
        "config": {
            "template_type": "sensor", "name": "Days", "state": "{{ 1 }}",
            "unit_of_measurement": "d",
        },
        "entities": ["sensor.of_e1"],
    }, text
    refused = lambda **kw: call("upsert_template_entity", **kw)
    # what the flow refuses is reported, its flow aborted, nothing left behind
    is_error, text = refused(template_type="bogus", config={"name": "n", "state": "s"})
    assert is_error and "type must be one of ['binary_sensor', 'sensor']" in text, text
    is_error, text = refused(template_type="sensor", config={"name": "n"})
    assert is_error and "required key not provided: ['state']" in text, text
    is_error, text = refused(template_type="sensor", config={"name": "n", "state": "bad"})
    assert is_error and "invalid config" in text and "invalid_template" in text, text
    assert aborted == ["f1"] * 3, aborted
    is_error, text = refused(
        template_type="sensor", config={"name": "n", "state": "s", "attributes": {}}
    )
    assert is_error and "['attributes'] are not fields" in text, text
    assert list(entries) == ["e1"], entries

    # a change replaces the options; a field the options form lacks is
    # refused before anything is saved
    is_error, text = refused(template_type="sensor", entry_id="e1", config={"state": "{{ 2 }}"})
    changed = {"template_type": "sensor", "name": "Days", "state": "{{ 2 }}"}
    assert not is_error and json.loads(text)["config"] == changed, text
    is_error, text = refused(template_type="sensor", entry_id="e1", config={"name": "X", "state": "s"})
    assert is_error and "['name'] cannot be changed" in text, text
    assert entries["e1"].options == changed and len(aborted) == 4
    is_error, text = refused(template_type="sensor", entry_id="nope", config={"state": "s"})
    assert is_error and "HTTP 404" in text, text
    # the type is part of the address
    is_error, text = refused(template_type="binary_sensor", entry_id="e1", config={"state": "s"})
    assert is_error and "HTTP 404" in text, text
    assert len(json.loads(call("list_template_entities", template_type="sensor")[1])["items"]) == 1
    assert json.loads(call("list_template_entities", template_type="binary_sensor")[1]) == {"items": []}

    # a flow of several forms: each gets the fields it asks for
    is_error, text = call(
        "upsert_history_stats_sensor",
        config={"name": "Rain", "entity_id": "binary_sensor.rain", "type": "time",
                "end": "{{ now() }}", "duration": {"hours": 2}},
    )
    assert not is_error and json.loads(text)["entry_id"] == "e2", text
    assert entries["e2"].options == {
        "name": "Rain", "entity_id": "binary_sensor.rain", "type": "time",
        "end": "{{ now() }}", "duration": {"hours": 2},
    }, entries["e2"].options
    assert [i["entry_id"] for i in json.loads(call("list_history_stats_sensors")[1])["items"]] == ["e2"]
    # an entry of another integration is not a history_stats sensor
    is_error, text = call("get_history_stats_sensor", entry_id="e1")
    assert is_error and "HTTP 404" in text, text
    assert call("delete_history_stats_sensor", entry_id="e2")[0] is False and "e2" not in entries
    is_error, text = call("delete_history_stats_sensor", entry_id="e2")
    assert is_error and "HTTP 404" in text, text
    assert call("delete_template_entity", template_type="sensor", entry_id="e1")[0] is False
    assert not entries

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
    async def fake_connect(hass, token, error):
        """A store that answers its websocket commands from `rows`."""
        tokens.append(token)

        async def command(type_, **fields):
            sent.append((type_, fields))
            if type_ in fail:
                raise error(fail[type_])
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
    from custom_components.automation_api import ha_client

    async def without_token():
        async with ha_client.connect(fake_hass, None, hacs.HacsError):
            pass

    assert "access token" in str(run(without_token()))

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
    HASS.config_entries = SimpleNamespace(
        async_entries=lambda domain: [1] if domain == "marketplace" else []
    )
    is_error, text = call("hacs_search", query="M/P")
    assert not is_error and json.loads(text)["items"][0]["full_name"] == "M/p", text
    assert tokens[-1] == "t0ken", tokens[-1]

    # Lovelace: dashboards are created, updated and deleted by Lovelace itself,
    # through its websocket commands; their config through its live objects
    import homeassistant.components.lovelace.const as lovelace_const

    from custom_components.automation_api import lovelace

    lovelace_const.DOMAIN = "lovelace"

    class Dashboard:
        """What Lovelace keeps per dashboard."""

        def __init__(self, config, mode="storage", saved=None):
            self.config, self.mode, self.saved = config, mode, saved

        async def async_load(self, force):
            if self.saved is None:
                raise RuntimeError("no config yet")
            return self.saved

        async def async_save(self, config):
            self.saved = config

    live = {
        None: Dashboard(None, saved={"views": []}),
        "from-yaml": Dashboard({"url_path": "from-yaml", "title": "Y"}, mode="yaml"),
    }
    lovelace_sent = []

    @asynccontextmanager
    async def fake_lovelace(hass, token, error):
        async def command(type_, **fields):
            lovelace_sent.append((token, type_, dict(fields)))
            command = type_.removeprefix("lovelace/dashboards/")
            if command == "create":
                if "-" not in fields["url_path"]:
                    raise error("Url path needs to contain a hyphen (-) (invalid_format)")
                item = {**fields, "id": fields["url_path"].replace("-", "_"), "mode": "storage"}
                live[fields["url_path"]] = Dashboard(item)
                return item
            dashboard_id = fields.pop("dashboard_id")
            dashboard = next(
                d for d in live.values() if d.config and d.config.get("id") == dashboard_id
            )
            if command == "update":
                dashboard.config = {**dashboard.config, **fields}
                return dashboard.config
            del live[dashboard.config["url_path"]]
            return None

        yield command

    lovelace._connect = fake_lovelace
    lovelace_hass = SimpleNamespace(data={"lovelace": SimpleNamespace(dashboards=live)})

    def lv(coro):
        try:
            return asyncio.run(coro)
        except ValueError as e:
            return e

    assert "hyphen" in str(lv(lovelace.create_dashboard(lovelace_hass, "t", "garden", "Garden")))
    created = lv(lovelace.create_dashboard(lovelace_hass, "t", "my-garden", "Garden", icon="mdi:sprout"))
    assert created["id"] == "my_garden", created
    assert lovelace_sent[-1] == (
        "t",
        "lovelace/dashboards/create",
        {
            "url_path": "my-garden", "title": "Garden", "icon": "mdi:sprout",
            "show_in_sidebar": True, "require_admin": False,
        },
    ), lovelace_sent[-1]
    # usable at once: Lovelace registered it, no restart and no storage file
    assert lv(lovelace.append_view(lovelace_hass, "my-garden", {"title": "V"})) == {"view_index": 0}
    assert live["my-garden"].saved == {"views": [{"title": "V"}]}
    listed = {d["url_path"]: d for d in lv(lovelace.list_dashboards(lovelace_hass))}
    assert listed["default"]["title"] == "Overview" and listed["from-yaml"]["mode"] == "yaml"
    assert listed["my-garden"]["title"] == "Garden" and listed["my-garden"]["icon"] == "mdi:sprout"

    updated = lv(lovelace.update_dashboard(lovelace_hass, "t", "my-garden", {"title": "G2"}))
    assert updated["title"] == "G2" and live["my-garden"].config["title"] == "G2"
    assert lovelace_sent[-1][1:] == (
        "lovelace/dashboards/update",
        {"dashboard_id": "my_garden", "title": "G2"},
    ), lovelace_sent[-1]
    update = lambda url_path, changes: str(
        lv(lovelace.update_dashboard(lovelace_hass, "t", url_path, changes))
    )
    assert "cannot change ['id']" in update("my-garden", {"id": "x"})
    assert "default dashboard" in update("default", {"title": "x"})
    assert "not a storage dashboard" in update("from-yaml", {"title": "x"})
    assert "not found" in update("no-such", {"title": "x"})

    assert lv(lovelace.get_config(lovelace_hass, "no-such")) is None
    assert "not found" in str(lv(lovelace.set_config(lovelace_hass, "no-such", {"views": []})))
    assert "'yaml' mode" in str(lv(lovelace.set_config(lovelace_hass, "from-yaml", {"views": []})))

    assert "default dashboard" in str(lv(lovelace.delete_dashboard(lovelace_hass, "t", "default")))
    assert lv(lovelace.delete_dashboard(lovelace_hass, "t", "my-garden")) is True
    assert "my-garden" not in live and lovelace_sent[-1][2] == {"dashboard_id": "my_garden"}
    assert lv(lovelace.delete_dashboard(lovelace_hass, "t", "my-garden")) is False

    # the views hand Lovelace the bearer token of the request
    is_error, text = call("create_dashboard", url_path="via-mcp", title="M")
    assert not is_error and json.loads(text)["dashboard"]["id"] == "via_mcp", text
    assert lovelace_sent[-1][0] == "t0ken", lovelace_sent[-1]

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

    # what happened: statistics, traces, the logbook and the system log are
    # read with Home Assistant's own websocket commands, as the caller
    from datetime import datetime, timedelta, timezone

    from custom_components.automation_api import history

    NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    history.dt_util = SimpleNamespace(
        utcnow=lambda: NOW, parse_datetime=datetime.fromisoformat, as_utc=lambda value: value
    )
    asked = []
    hour = 3600 * 1000
    t0 = int(NOW.timestamp() * 1000) - 2 * hour

    @asynccontextmanager
    async def fake_history(hass, token, error):
        async def command(type_, **fields):
            asked.append((token, type_, fields))
            if type_ == "recorder/list_statistic_ids":
                return [
                    {"statistic_id": "sensor.temp", "name": None, "has_mean": True, "has_sum": False,
                     "display_unit_of_measurement": "°C", "unit_class": "temperature", "source": "recorder", "mean_type": 1},
                    {"statistic_id": "energy:grid", "name": "Grid import", "has_mean": False, "has_sum": True,
                     "display_unit_of_measurement": "kWh", "unit_class": "energy", "source": "energy"},
                ]
            if type_ == "recorder/statistics_during_period":
                return {
                    "sensor.temp": [
                        {"start": t0, "end": t0 + hour, "mean": 20.5, "min": 20.0, "max": 21.0, "last_reset": None},
                        {"start": t0 + hour, "end": t0 + 2 * hour, "mean": 21.5, "min": 21.0, "max": 22.0, "last_reset": None},
                    ]
                }
            if type_ == "trace/list":
                return [
                    {"run_id": "old", "item_id": "a1", "timestamp": {"start": "2026-11-26T08:00:00+00:00"}},
                    {"run_id": "new", "item_id": "a1", "timestamp": {"start": "2026-11-26T09:00:00+00:00"}},
                ]
            if type_ == "trace/get":
                if fields["run_id"] == "gone":
                    raise error("The trace could not be found (not_found)")
                return {"run_id": fields["run_id"], "trace": {"trigger/0": [{"path": "trigger/0"}]}}
            if type_ == "logbook/get_events":
                return [{"entity_id": f"light.l{i}", "state": "on", "when": NOW.timestamp() - 60 + i} for i in range(5)]
            if type_ == "system_log/list":
                return [
                    {"name": "homeassistant.core", "message": ["first line", "second line"], "level": "ERROR",
                     "source": ["core.py", 2962], "timestamp": NOW.timestamp() - 10, "exception": "Traceback: boom",
                     "count": 2, "first_occurred": NOW.timestamp() - 100},
                    {"name": "custom.thing", "message": ["slow update"], "level": "WARNING", "source": ["x.py", 1],
                     "timestamp": NOW.timestamp() - 5, "exception": "", "count": 1, "first_occurred": NOW.timestamp() - 5},
                ]
            raise AssertionError(type_)

        yield command

    history._connect = fake_history

    def read(tool_name, **arguments):
        is_error, text = call(tool_name, **arguments)
        assert not is_error, text
        return json.loads(text)

    ids = read("list_statistics", search="GRID", statistic_type="sum")["items"]
    assert ids == [
        {"statistic_id": "energy:grid", "name": "Grid import", "unit_of_measurement": "kWh",
         "unit_class": "energy", "has_mean": False, "has_sum": True, "source": "energy"}
    ], ids
    assert asked[-1] == ("t0ken", "recorder/list_statistic_ids", {"statistic_type": "sum"})
    assert len(read("list_statistics")["items"]) == 2 and asked[-1][2] == {}
    is_error, text = call("list_statistics", statistic_type="bogus")
    assert is_error and "HTTP 400" in text, text

    stats = read("get_statistics", statistic_id="sensor.temp, sensor.other", hours=2, types="mean,max")
    assert stats["period"] == "hour" and stats["start"] == "2026-11-26T10:00:00+00:00", stats
    assert stats["items"]["sensor.temp"][0] == {
        "start": "2026-11-26T10:00:00+00:00", "end": "2026-11-26T11:00:00+00:00",
        "mean": 20.5, "min": 20.0, "max": 21.0,
    }, stats["items"]
    assert asked[-1][2] == {
        "start_time": "2026-11-26T10:00:00+00:00", "end_time": "2026-11-26T12:00:00+00:00",
        "statistic_ids": ["sensor.temp", "sensor.other"], "period": "hour", "types": ["mean", "max"],
    }, asked[-1]
    # an answer too large to be useful is refused before it is asked for
    before = len(asked)
    is_error, text = call("get_statistics", statistic_id="sensor.temp", period="5minute", days=30)
    assert is_error and "about 8640 rows" in text and len(asked) == before, text
    assert not call("get_statistics", statistic_id="sensor.temp", period="day", days=365)[0]
    is_error, text = call("get_statistics", statistic_id="sensor.temp", period="decade")
    assert is_error and "period must be one of" in text, text

    # traces: by the id of the automation, or by its entity id
    HASS.states = SimpleNamespace(
        get={"automation.living_room": SimpleNamespace(attributes={"id": "a1"})}.get
    )
    runs = read("list_traces", item_id="automation.living_room")["items"]
    assert [r["run_id"] for r in runs] == ["new", "old"], runs
    assert asked[-1][1:] == ("trace/list", {"domain": "automation", "item_id": "a1"})
    read("list_traces", item_id="a1")
    assert asked[-1][2] == {"domain": "automation", "item_id": "a1"}
    read("list_traces", domain="script", item_id="script.water")
    assert asked[-1][2] == {"domain": "script", "item_id": "water"}
    read("list_traces")
    assert asked[-1][2] == {"domain": "automation"}
    is_error, text = call("list_traces", domain="scene")
    assert is_error and "domain must be one of" in text, text
    assert read("get_trace", item_id="living_room", run_id="new")["trace"] == {"trigger/0": [{"path": "trigger/0"}]}
    assert asked[-1][2] == {"domain": "automation", "item_id": "a1", "run_id": "new"}
    is_error, text = call("get_trace", item_id="a1", run_id="gone")
    assert is_error and "HTTP 404" in text, text

    book = read("get_logbook", entity_id="light.l1,light.l2", hours=1, limit=2)
    assert book["total"] == 5 and [e["entity_id"] for e in book["items"]] == ["light.l3", "light.l4"], book
    assert book["items"][-1]["when"] == "2026-11-26T11:59:04+00:00", book["items"][-1]
    assert asked[-1][2] == {
        "start_time": "2026-11-26T11:00:00+00:00", "end_time": "2026-11-26T12:00:00+00:00",
        "entity_ids": ["light.l1", "light.l2"],
    }, asked[-1]
    assert "entity_ids" not in (read("get_logbook"), asked[-1][2])[1]
    is_error, text = call("get_logbook", limit=0)
    assert is_error and "limit must be at least 1" in text, text

    log = read("get_system_log")
    assert log["total"] == 2 and [e["level"] for e in log["items"]] == ["WARNING", "ERROR"], log
    assert log["items"][1] == {
        "level": "ERROR", "logger": "homeassistant.core", "message": "first line\nsecond line",
        "exception": "Traceback: boom", "source": "core.py:2962", "count": 2,
        "first_occurred": "2026-11-26T11:58:20+00:00", "last_occurred": "2026-11-26T11:59:50+00:00",
    }, log["items"][1]
    assert log["items"][0]["exception"] is None
    assert [e["logger"] for e in read("get_system_log", level="error")["items"]] == ["homeassistant.core"]
    assert [e["logger"] for e in read("get_system_log", search="BOOM")["items"]] == ["homeassistant.core"]
    newest = read("get_system_log", limit=1)
    assert newest["total"] == 2 and [e["level"] for e in newest["items"]] == ["WARNING"], newest

    # operations: service calls whose effect can be read back afterwards
    from custom_components.automation_api import operations

    contexts = iter(range(1000))
    clock = [100]
    operations.Context = lambda user_id: SimpleNamespace(id=next(contexts), user_id=user_id)
    operations.dt_util = SimpleNamespace(utcnow=lambda: clock[0])
    operations.SupportsResponse = SimpleNamespace(NONE="none")

    world, made, slow = {}, [], {"light.slow"}
    areas = {"garden": ["light.g1", "light.g2"]}

    def write(entity_id, state, context):
        clock[0] += 1
        world[entity_id] = SimpleNamespace(
            entity_id=entity_id, state=state, context=context, last_updated=clock[0]
        )

    async def fake_service(domain, service, data, blocking, context, return_response):
        made.append((domain, service, data))
        if service == "explode":
            raise ValueError("boom")
        named = data.get("entity_id") or []
        named = named.split(",") if isinstance(named, str) else named
        for entity_id in [e.strip() for e in named] + areas.get(data.get("area_id"), []):
            if entity_id not in slow:  # a slow device answers after the call
                write(entity_id, "on" if service == "turn_on" else "off", context)
        return {"answer": 42} if return_response else None

    HASS.data = {}
    HASS.services = SimpleNamespace(
        async_call=fake_service,
        has_service=lambda domain, service: service != "no_such",
        supports_response=lambda domain, service: "only" if service == "ask" else "none",
    )
    HASS.states = SimpleNamespace(async_all=lambda: list(world.values()), get=world.get)

    def status(*ids, timeout=0):
        is_error, text = call("get_operation_status", operation_ids=list(ids), timeout_seconds=timeout)
        assert not is_error, text
        return json.loads(text)["operations"]

    is_error, text = call(
        "call_service", domain="light", service="turn_on", data={"entity_id": "light.a"}
    )
    first = json.loads(text)
    assert not is_error and first["status"] == "ok" and first["response"] is None, text
    assert status(first["operation_id"]) == [
        {
            "operation_id": first["operation_id"], "domain": "light", "service": "turn_on",
            "status": "completed", "error": None,
            "entities": [{"entity_id": "light.a", "state": "on", "changed": True}],
        }
    ]
    assert json.loads(call("call_service", domain="x", service="ask")[1])["response"] == {"answer": 42}

    # several calls at once: each is reported, a failing one stops nothing
    is_error, text = call(
        "bulk_control",
        operations=[
            {"domain": "light", "service": "turn_off", "data": {"entity_id": "light.a, light.b"}},
            {"domain": "light", "service": "explode", "data": {"entity_id": "light.c"}},
            {"service": "turn_on"},
            {"domain": "light", "service": "turn_on", "data": {"area_id": "garden"}},
            {"domain": "light", "service": "turn_on", "data": {"entity_id": ["light.slow"]}},
        ],
    )
    bulk = json.loads(text)
    assert not is_error and bulk["status"] == "failed", text
    off, exploded, invalid, garden, slow_one = bulk["results"]
    assert [r["status"] for r in bulk["results"]] == ["ok", "failed", "failed", "ok", "ok"]
    assert "boom" in exploded["error"] and invalid["operation_id"] is None, bulk
    assert world["light.a"].state == "off" and world["light.b"].state == "off"
    by_id = {
        o["operation_id"]: o
        for o in status(off["operation_id"], exploded["operation_id"], garden["operation_id"],
                        slow_one["operation_id"], "nope")
    }
    assert by_id[off["operation_id"]]["status"] == "completed"
    assert [e["entity_id"] for e in by_id[off["operation_id"]]["entities"]] == ["light.a", "light.b"]
    failed = by_id[exploded["operation_id"]]
    assert failed["status"] == "failed" and "boom" in failed["error"], failed
    assert failed["entities"] == [{"entity_id": "light.c", "state": None, "changed": False}]
    # entities reached through an area are found by the context of the call
    assert by_id[garden["operation_id"]]["status"] == "completed"
    assert [e["entity_id"] for e in by_id[garden["operation_id"]]["entities"]] == ["light.g1", "light.g2"]
    assert by_id["nope"] == {"operation_id": "nope", "status": "unknown"}
    # a named entity that has not followed yet: unconfirmed, until it does
    assert by_id[slow_one["operation_id"]]["status"] == "unconfirmed"
    assert by_id[slow_one["operation_id"]]["entities"] == [
        {"entity_id": "light.slow", "state": None, "changed": False}
    ]
    naps = []

    async def nap(seconds):
        naps.append(seconds)
        if len(naps) == 3:  # the device answers, without the context of the call
            write("light.slow", "on", SimpleNamespace(id="its own"))

    operations.asyncio = SimpleNamespace(sleep=nap, gather=asyncio.gather)
    assert status(slow_one["operation_id"], timeout=0.5)[0]["status"] == "unconfirmed"
    assert len(naps) == 2, naps
    late = status(slow_one["operation_id"], timeout=30)[0]
    assert late["status"] == "completed" and late["entities"][0]["state"] == "on", late
    assert len(naps) == 3, naps

    # a failing single call is a tool error that names its operation
    is_error, text = call("call_service", domain="light", service="explode")
    assert is_error and "boom" in text and "operation_id" in text, text
    is_error, text = call("call_service", domain="light", service="no_such")
    assert is_error and "the service light.no_such does not exist" in text, text
    # one after the other, in the given order
    made.clear()
    call(
        "bulk_control", parallel=False,
        operations=[{"domain": "a", "service": "turn_on"}, {"domain": "b", "service": "turn_on"}],
    )
    assert [m[0] for m in made] == ["a", "b"], made
    # only the latest operations are remembered
    operations._KEPT = 2
    kept = json.loads(call("call_service", domain="a", service="turn_on")[1])["operation_id"]
    assert status(first["operation_id"])[0]["status"] == "unknown"
    assert status(kept)[0]["status"] == "completed" and len(HASS.data["automation_api"]["operations"]) == 2

    print("ok")


if __name__ == "__main__":
    main()
