"""MCP server (Streamable HTTP) served straight from the integration.

One endpoint, POST /api/automation_api/mcp, speaking stateless JSON-RPC:
initialize, ping, tools/list and tools/call. Auth is HA's own bearer token
(administrator), exactly like the REST endpoints.

Every tool dispatches in-process to the REST views in http.py, so validation,
logging and reloads live in one place.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_dumps

from . import http as views
from . import operations as operations_mod
from . import stats as stats_mod
from .const import LOG_FILE

SERVER_INFO = {"name": "ha-automation-api", "version": "2.3.0"}
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

_JSON_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}

# name -> (coroutine function, MCP tool definition)
TOOLS: dict[str, tuple[Any, dict[str, Any]]] = {}


def tool(fn):
    """Register `fn` as an MCP tool; the input schema comes from its signature."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in list(inspect.signature(fn).parameters.values())[1:]:  # skip ctx
        # Annotations are strings (PEP 563): "str", "dict[str, Any] | None", ...
        base = p.annotation.removesuffix(" | None").split("[")[0]
        props[p.name] = {"type": _JSON_TYPES[base]}
        if p.default is p.empty:
            required.append(p.name)
    TOOLS[fn.__name__] = (
        fn,
        {
            "name": fn.__name__,
            "description": inspect.getdoc(fn) or "",
            "inputSchema": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    )
    return fn


class _Request(dict):
    """Just enough of aiohttp's Request for the views in http.py."""

    def __init__(self, request, query: dict[str, str], body: Any):
        super().__init__(hass_user=request["hass_user"])
        self.app = request.app
        self.headers = request.headers
        self.query = query
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _query_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


class _Ctx:
    """Per-call context handed to every tool as its first argument."""

    def __init__(self, request):
        self.request = request
        self.hass: HomeAssistant = request.app["hass"]

    async def __call__(self, view, method: str, *, query=None, body=None, **path):
        """Run a REST view in-process and return its decoded JSON body."""
        query = {
            k: _query_value(v)
            for k, v in (query or {}).items()
            if v not in (None, "")
        }
        resp = await getattr(view(), method)(
            _Request(self.request, query, body), **path
        )
        data = json.loads(resp.body)
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}: {data}")
        return data


# ---------------------------------------------------------------------------
# Automations
# ---------------------------------------------------------------------------


@tool
async def list_automations(c) -> Any:
    """List every automation known to Home Assistant.

    Returns the entity_id, slug id, friendly name, current state, and last
    trigger timestamp for each automation (as reported by the state machine).
    """
    return await c(views.AutomationApiView, "get")


@tool
async def get_automation(c, automation_id: str) -> Any:
    """Get a single automation by id (e.g. 'solaredge_power_notify').

    Returns live state + attributes. Use `get_automation_yaml` for the raw
    YAML config (triggers / conditions / actions).
    """
    return await c(views.AutomationApiView, "get", query={"id": automation_id})


@tool
async def get_automation_yaml(c, automation_id: str | None = None) -> Any:
    """Return raw YAML config from automations.yaml.

    Pass `automation_id` for a single automation, or omit to get everything.
    """
    return await c(views.AutomationApiYamlView, "get", query={"id": automation_id})


@tool
async def create_or_update_automation(
    c,
    id: str,
    name: str,
    trigger: list[dict[str, Any]],
    action: list[dict[str, Any]],
    condition: list[dict[str, Any]] | None = None,
    description: str = "",
    mode: str = "single",
) -> Any:
    """Create or update an automation, as the automation editor saves it:
    Home Assistant validates the config, stores it and reloads it.

    Args:
        id: Stable slug (no 'automation.' prefix), e.g. 'living_room_lights_on'.
        name: Friendly name shown in the UI.
        trigger: List of HA trigger dicts.
        action: List of HA action dicts.
        condition: Optional list of HA condition dicts.
        description: Free-form description.
        mode: HA mode — 'single', 'restart', 'queued', or 'parallel'.
    """
    body = {
        "id": id,
        "name": name,
        "trigger": trigger,
        "action": action,
        "condition": condition or [],
        "description": description,
        "mode": mode,
    }
    return await c(views.AutomationApiView, "post", body=body)


@tool
async def delete_automation(c, automation_id: str) -> Any:
    """Delete an automation by id, as the automation editor does."""
    return await c(views.AutomationApiView, "delete", query={"id": automation_id})


@tool
async def trigger_automation(c, automation_id: str) -> Any:
    """Manually fire an automation's actions, bypassing triggers + conditions.

    Accepts either the slug ('my_auto') or the full entity_id ('automation.my_auto').
    """
    return await c(views.AutomationApiTriggerView, "post", body={"id": automation_id})


@tool
async def list_areas(c) -> Any:
    """List every area (room) defined in Home Assistant."""
    return await c(views.AutomationApiAreasView, "get")


@tool
async def list_entities(
    c,
    domain: str | None = None,
    area: str | None = None,
    search: str | None = None,
) -> Any:
    """List Home Assistant entities with optional filters.

    Args:
        domain: Filter by domain, e.g. 'light', 'switch', 'sensor'.
        area:   Filter by area name (case-insensitive), e.g. 'Woonkamer'.
        search: Substring match against entity_id or friendly name.
    """
    return await c(
        views.AutomationApiEntitiesView,
        "get",
        query={"domain": domain, "area": area, "search": search},
    )


@tool
async def get_automation_api_log(c) -> Any:
    """Return the Automation API log file (automation_api.log).

    Contains a record of every create/update/delete/trigger the integration
    has performed via REST or MCP.
    """
    # The REST view streams a FileResponse; read the file directly instead.
    path = Path(c.hass.config.path(LOG_FILE))
    return await c.hass.async_add_executor_job(path.read_text)


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------


@tool
async def get_script(c, script_id: str) -> Any:
    """Return the stored config of a script by its id (the part after
    'script.'), e.g. {"alias": ..., "sequence": [...], "mode": ...}."""
    return await c(views.ScriptItemView, "get", item_id=script_id)


@tool
async def create_or_update_script(c, script_id: str, config: dict[str, Any]) -> Any:
    """Create or replace a script, as the script editor saves it: Home
    Assistant validates the config, stores it and reloads it.

    Args:
        script_id: Slug that becomes the entity id 'script.<script_id>'.
        config: Full script config, e.g. {"alias": "Water given",
            "sequence": [{"action": "input_datetime.set_datetime", ...}],
            "mode": "single", "icon": "mdi:watering-can", "fields": {...}}.

    A script with `fields` that calls several notify services is the way to
    get a notify group that passes `data` on.
    """
    return await c(views.ScriptItemView, "put", body=config, item_id=script_id)


@tool
async def delete_script(c, script_id: str) -> Any:
    """Delete a script by its id, as the script editor does."""
    return await c(views.ScriptItemView, "delete", item_id=script_id)


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------


@tool
async def get_scene(c, scene_id: str) -> Any:
    """Return the stored config of a scene by its id, e.g.
    {"id": ..., "name": ..., "entities": {...}}. Only scenes made in the UI
    or here have an id; it is the `id` attribute of the scene's state."""
    return await c(views.SceneItemView, "get", item_id=scene_id)


@tool
async def create_or_update_scene(c, scene_id: str, config: dict[str, Any]) -> Any:
    """Create or replace a scene, as the scene editor saves it: Home
    Assistant validates the config, stores it and reloads the scenes.

    Args:
        scene_id: Id of the scene; any stable string, e.g. 'movie_night'.
        config: {"name": "Movie night", "entities": {"light.tv_lamp":
            {"state": "on", "brightness": 80}, "cover.blinds": "closed"},
            "icon": "mdi:movie"}. `entities` maps each entity to the state,
            or the state with attributes, the scene sets it to.

    The result holds the scene's `entity_id`, which Home Assistant derives
    from `name`. Activate the scene with `call_service`: domain 'scene',
    service 'turn_on', data {"entity_id": ...}.
    """
    return await c(views.SceneItemView, "put", body=config, item_id=scene_id)


@tool
async def delete_scene(c, scene_id: str) -> Any:
    """Delete a scene by its id, as the scene editor does."""
    return await c(views.SceneItemView, "delete", item_id=scene_id)


# ---------------------------------------------------------------------------
# Helpers — ordinary UI helpers, as made under Settings → Helpers
# ---------------------------------------------------------------------------


@tool
async def upsert_helper(
    c, domain: str, config: dict[str, Any], helper_id: str | None = None
) -> Any:
    """Create an input helper, or replace the config of an existing one.

    Without `helper_id` a helper is created; Home Assistant derives its id
    and entity id from `name` ('Garden start' → input_datetime.garden_start).
    With `helper_id` (from `list_helpers`) that helper gets `config` as its
    full new config. The result holds the helper with its `id` and
    `entity_id`. It is live at once and editable in the UI.

    Args:
        domain: One of input_boolean / input_button / input_datetime /
                input_number / input_select / input_text.
        config: Full config of the helper, `name` included. Examples:
            input_boolean → {"name": "...", "icon": "mdi:bell"}
            input_datetime → {"name": "...", "has_date": true, "has_time": false}
            input_number  → {"name": "...", "min": 0, "max": 100, "step": 1}
            input_select  → {"name": "...", "options": ["a", "b"]}
    """
    if helper_id is None:
        return await c(views.HelpersListView, "post", body=config, domain=domain)
    return await c(
        views.HelperItemView, "put", body=config, domain=domain, helper_id=helper_id
    )


@tool
async def delete_helper(c, domain: str, helper_id: str) -> Any:
    """Delete an input helper by its id (from `list_helpers`)."""
    return await c(views.HelperItemView, "delete", domain=domain, helper_id=helper_id)


@tool
async def list_helpers(c, domain: str) -> Any:
    """List the UI helpers of the given domain, by id, each with its config
    and `entity_id`. Helpers defined in YAML are not listed."""
    return await c(views.HelpersListView, "get", domain=domain)


@tool
async def get_helper(c, domain: str, helper_id: str) -> Any:
    """Return the config and `entity_id` of a single UI helper."""
    return await c(views.HelperItemView, "get", domain=domain, helper_id=helper_id)


@tool
async def upsert_template_entity(
    c, template_type: str, config: dict[str, Any], entry_id: str | None = None
) -> Any:
    """Create a template entity, or change an existing one, as the Template
    helper under Settings → Helpers does. It is live at once.

    Without `entry_id` an entity is created from `config`, which needs
    `name`. With `entry_id` (from `list_template_entities`) `config` replaces
    the options of that entity; `name` can't be changed there, and optional
    fields left out are cleared. The result holds `entry_id`, `config` and
    the `entities` created.

    Args:
        template_type: sensor, binary_sensor, switch, button, number, select
            or any other type the Template helper offers.
        config: The fields of the helper's form, e.g. for a sensor
            {"name": "Days since watering", "state": "{{ ... }}",
             "unit_of_measurement": "d", "device_class": "duration",
             "state_class": "measurement"}; `additional_options` holds
            {"availability": "{{ ... }}"}. Only what the form offers is
            accepted: no attributes, icon templates or triggers. Set a fixed
            icon with `update_registry_entity`.
    """
    if entry_id is None:
        return await c(views.TemplateListView, "post", body=config, kind=template_type)
    return await c(
        views.TemplateItemView, "put", body=config, kind=template_type, entry_id=entry_id
    )


@tool
async def delete_template_entity(c, template_type: str, entry_id: str) -> Any:
    """Delete a template entity made as a helper, by type and `entry_id`."""
    return await c(
        views.TemplateItemView, "delete", kind=template_type, entry_id=entry_id
    )


@tool
async def list_template_entities(c, template_type: str) -> Any:
    """List the template entities of the given type made as helpers, each
    with `entry_id`, `config` and `entities`. Template entities defined in
    YAML are not listed."""
    return await c(views.TemplateListView, "get", kind=template_type)


@tool
async def get_template_entity(c, template_type: str, entry_id: str) -> Any:
    """Return the config of a single template entity by type and `entry_id`."""
    return await c(views.TemplateItemView, "get", kind=template_type, entry_id=entry_id)


@tool
async def upsert_history_stats_sensor(
    c, config: dict[str, Any], entry_id: str | None = None
) -> Any:
    """Create a history_stats sensor, or change an existing one, as the
    History stats helper under Settings → Helpers does. It is live at once.

    Without `entry_id` a sensor is created from `config`. With `entry_id`
    (from `list_history_stats_sensors`) `config` replaces its options: only
    start, end, duration and state_class can be changed, and optional fields
    left out are cleared. Example config:
        {"name": "Rain last 2h", "entity_id": "binary_sensor.raining",
         "state": ["on"], "type": "time",
         "end": "{{ now() }}", "duration": {"hours": 2}}
    Exactly two of start, end and duration are needed.
    """
    if entry_id is None:
        return await c(views.HistoryStatsListView, "post", body=config)
    return await c(views.HistoryStatsItemView, "put", body=config, entry_id=entry_id)


@tool
async def delete_history_stats_sensor(c, entry_id: str) -> Any:
    """Delete a history_stats sensor made as a helper, by `entry_id`."""
    return await c(views.HistoryStatsItemView, "delete", entry_id=entry_id)


@tool
async def list_history_stats_sensors(c) -> Any:
    """List the history_stats sensors made as helpers, each with `entry_id`,
    `config` and `entities`. Sensors defined in YAML are not listed."""
    return await c(views.HistoryStatsListView, "get")


@tool
async def get_history_stats_sensor(c, entry_id: str) -> Any:
    """Return the config of a single history_stats sensor by `entry_id`."""
    return await c(views.HistoryStatsItemView, "get", entry_id=entry_id)


@tool
async def reload_config(c, domains: list[str] | None = None) -> Any:
    """Reload HA config.

    With no arguments → calls homeassistant.reload_all.
    With a list of domains → reloads each individually (input_boolean,
    input_datetime, template, automation, script, scene, ...).
    """
    return await c(
        views.ReloadView, "post", body={"domains": domains} if domains else {}
    )


@tool
async def restart_home_assistant(c) -> Any:
    """Schedule a Home Assistant restart (fire-and-forget).

    Needed after installing an integration; helpers, automations, scripts
    and dashboards made here are live without one.
    """
    return await c(views.RestartView, "post")


# ---------------------------------------------------------------------------
# Lovelace dashboards — use "default" as url_path for the Overview dashboard
# ---------------------------------------------------------------------------


@tool
async def list_dashboards(c) -> Any:
    """List every Lovelace dashboard (url_path, title, mode, icon)."""
    return await c(views.LovelaceDashboardsView, "get")


@tool
async def create_dashboard(
    c,
    url_path: str,
    title: str,
    icon: str | None = None,
    show_in_sidebar: bool = True,
    require_admin: bool = False,
) -> Any:
    """Create a new storage-mode Lovelace dashboard, as Settings → Dashboards
    does.

    The dashboard is immediately visible under Settings → Dashboards and at
    `/<url_path>`, without a restart. It starts empty; add views and cards
    separately.

    Args:
        url_path: URL slug with a hyphen, not in use yet — e.g. 'my-garden'
            yields /my-garden.
        title: Sidebar title.
        icon: MDI icon, e.g. 'mdi:sprout'.
        show_in_sidebar: Whether to show in the left sidebar.
        require_admin: Admin‑only access.
    """
    body = {
        "url_path": url_path,
        "title": title,
        "icon": icon,
        "show_in_sidebar": show_in_sidebar,
        "require_admin": require_admin,
    }
    return await c(views.LovelaceDashboardsView, "post", body=body)


@tool
async def update_dashboard_metadata(c, url_path: str, changes: dict[str, Any]) -> Any:
    """Update dashboard metadata: `changes` holds any of title, icon (null
    removes it), show_in_sidebar and require_admin.

    Cannot modify the default/Overview dashboard's metadata.
    """
    return await c(
        views.LovelaceDashboardItemView, "patch", body=changes, url_path=url_path
    )


@tool
async def delete_dashboard(c, url_path: str) -> Any:
    """Delete a custom dashboard. The default Overview cannot be deleted."""
    return await c(views.LovelaceDashboardItemView, "delete", url_path=url_path)


@tool
async def get_dashboard_config(c, url_path: str = "default") -> Any:
    """Return the full Lovelace config of a dashboard.

    Structure: {"title": ..., "views": [{"title": ..., "cards": [...]}, ...]}.
    """
    return await c(views.LovelaceConfigView, "get", url_path=url_path)


@tool
async def set_dashboard_config(c, url_path: str, config: dict[str, Any]) -> Any:
    """Overwrite the entire Lovelace config of a dashboard.

    `config` must contain at least `{"views": [...]}`.
    """
    return await c(views.LovelaceConfigView, "put", body=config, url_path=url_path)


@tool
async def append_dashboard_view(c, url_path: str, view: dict[str, Any]) -> Any:
    """Append a view to a dashboard.

    A view looks like::
        {"title": "Moestuin", "path": "moestuin", "icon": "mdi:sprout",
         "cards": [ ... ]}
    """
    return await c(views.LovelaceViewView, "post", body=view, url_path=url_path)


@tool
async def replace_dashboard_view(
    c, url_path: str, view_index: int, view: dict[str, Any]
) -> Any:
    """Replace the view at the given index."""
    return await c(
        views.LovelaceViewItemView,
        "put",
        body=view,
        url_path=url_path,
        view_index=view_index,
    )


@tool
async def delete_dashboard_view(c, url_path: str, view_index: int) -> Any:
    """Delete the view at the given index."""
    return await c(
        views.LovelaceViewItemView, "delete", url_path=url_path, view_index=view_index
    )


@tool
async def append_dashboard_card(
    c, url_path: str, view_index: int, card: dict[str, Any]
) -> Any:
    """Append a card to the given view.

    `card` is any valid Lovelace card config (e.g. `{"type": "entities", ...}`).
    """
    return await c(
        views.LovelaceCardView,
        "post",
        body=card,
        url_path=url_path,
        view_index=view_index,
    )


@tool
async def replace_dashboard_card(
    c, url_path: str, view_index: int, card_index: int, card: dict[str, Any]
) -> Any:
    """Replace a specific card within a view."""
    return await c(
        views.LovelaceCardItemView,
        "put",
        body=card,
        url_path=url_path,
        view_index=view_index,
        card_index=card_index,
    )


@tool
async def delete_dashboard_card(
    c, url_path: str, view_index: int, card_index: int
) -> Any:
    """Delete a specific card from a view."""
    return await c(
        views.LovelaceCardItemView,
        "delete",
        url_path=url_path,
        view_index=view_index,
        card_index=card_index,
    )


# ---------------------------------------------------------------------------
# Registry management
# ---------------------------------------------------------------------------


@tool
async def list_registry_entities(
    c,
    domain: str | None = None,
    platform: str | None = None,
    device_id: str | None = None,
    area_id: str | None = None,
    config_entry_id: str | None = None,
    disabled: bool | None = None,
) -> Any:
    """List entries from HA's entity_registry with full details.

    Returns each entity with `unique_id`, `platform` (integration name),
    `device_id`, `area_id`, `name` (user-set), `original_name` (from
    integration), `disabled_by`, `hidden_by`, `config_entry_id`. Use this
    to find duplicates, orphans, or filter by integration.
    """
    return await c(
        views.EntityRegistryListView,
        "get",
        query={
            "domain": domain,
            "platform": platform,
            "device_id": device_id,
            "area_id": area_id,
            "config_entry_id": config_entry_id,
            "disabled": disabled,
        },
    )


@tool
async def get_registry_entity(c, entity_id: str) -> Any:
    """Get full registry details for a single entity_id."""
    return await c(views.EntityRegistryItemView, "get", entity_id=entity_id)


@tool
async def update_registry_entity(c, entity_id: str, changes: dict[str, Any]) -> Any:
    """Mutate a registry entity. Supported keys in `changes`:
    `name`, `icon`, `area_id`, `new_entity_id` (rename),
    `disabled_by` (bool), `hidden_by` (bool).
    """
    return await c(
        views.EntityRegistryItemView, "patch", body=changes, entity_id=entity_id
    )


@tool
async def delete_registry_entity(c, entity_id: str) -> Any:
    """Remove an entity from the registry. Active integrations may re-add
    it on reload — use `delete_device` or `remove_config_entry` instead
    if the entity belongs to a still-active integration."""
    return await c(views.EntityRegistryItemView, "delete", entity_id=entity_id)


@tool
async def list_devices(
    c,
    area_id: str | None = None,
    manufacturer: str | None = None,
    model: str | None = None,
    integration: str | None = None,
    config_entry_id: str | None = None,
    disabled: bool | None = None,
) -> Any:
    """List devices from HA's device_registry.

    Each item has `id`, `name`, `name_by_user`, `manufacturer`, `model`,
    `area_id`, `config_entries` (list of entry ids), `identifiers`,
    `connections`, `disabled_by`. Filter by integration to scope (e.g.
    'tuya' to find all Tuya devices).
    """
    return await c(
        views.DeviceRegistryListView,
        "get",
        query={
            "area_id": area_id,
            "manufacturer": manufacturer,
            "model": model,
            "integration": integration,
            "config_entry_id": config_entry_id,
            "disabled": disabled,
        },
    )


@tool
async def get_device(c, device_id: str) -> Any:
    """Get full registry details for a single device id."""
    return await c(views.DeviceRegistryItemView, "get", device_id=device_id)


@tool
async def update_device(c, device_id: str, changes: dict[str, Any]) -> Any:
    """Mutate a device. Supported keys: `name_by_user`, `area_id`,
    `disabled_by` (bool)."""
    return await c(
        views.DeviceRegistryItemView, "patch", body=changes, device_id=device_id
    )


@tool
async def delete_device(c, device_id: str) -> Any:
    """Remove a device from the registry. Cascades to all entities of
    that device. The owning integration may re-add it on next discovery."""
    return await c(views.DeviceRegistryItemView, "delete", device_id=device_id)


@tool
async def list_config_entries(c, domain: str | None = None) -> Any:
    """List installed integrations (config entries).

    Each item has `entry_id`, `domain`, `title`, `state`, `host` (the
    device address, for local integrations), `disabled_by`,
    `supports_unload`, `supports_remove_device`. For `setup_error` or
    `setup_retry`, `reason` (or `reason_translation_key` with
    `reason_placeholders`) says why. Filter by `domain`
    (integration name like 'tuya' or 'wiz').
    """
    return await c(views.ConfigEntryListView, "get", query={"domain": domain})


@tool
async def get_config_entry(c, entry_id: str) -> Any:
    """Get full details for a single config entry."""
    return await c(views.ConfigEntryItemView, "get", entry_id=entry_id)


@tool
async def set_config_entry_host(c, entry_id: str, host: str) -> Any:
    """Point a local integration at a new IP address or hostname and reload
    it, e.g. after the router handed the device a new DHCP address. Keeps
    the entry, its devices, entities and history. Works for any integration
    that stores `host` or `ip_address`, also those without a reconfigure
    flow. Prefer the integration's own flow when it updates the address
    (WiZ: `start_config_flow` with the new host). Check the device really is
    at `host` first; the returned `state` shows whether setup succeeded."""
    return await c(
        views.ConfigEntryItemView, "patch", entry_id=entry_id, body={"host": host}
    )


@tool
async def reload_config_entry(c, entry_id: str) -> Any:
    """Reload an integration without restarting HA."""
    return await c(
        views.ConfigEntryActionView, "post", entry_id=entry_id, action="reload"
    )


@tool
async def disable_config_entry(c, entry_id: str) -> Any:
    """Soft-disable an integration (its devices/entities go unavailable
    but registry entries remain)."""
    return await c(
        views.ConfigEntryActionView, "post", entry_id=entry_id, action="disable"
    )


@tool
async def enable_config_entry(c, entry_id: str) -> Any:
    """Re-enable a previously disabled integration."""
    return await c(
        views.ConfigEntryActionView, "post", entry_id=entry_id, action="enable"
    )


@tool
async def remove_config_entry(c, entry_id: str) -> Any:
    """Fully remove an integration. Cascades: all its devices and
    entities are removed from the registries. Irreversible — to restore,
    re-add the integration via Settings → Devices & services."""
    return await c(views.ConfigEntryItemView, "delete", entry_id=entry_id)


@tool
async def list_config_flows(c) -> Any:
    """List config flows in progress: integrations Home Assistant discovered
    (zeroconf, DHCP, bluetooth, ...) that wait to be set up, plus unfinished
    flows. Finish one with `continue_config_flow`, using its `flow_id`."""
    return await c(views.ConfigFlowListView, "get")


@tool
async def start_config_flow(c, domain: str) -> Any:
    """Add an integration by starting its config flow, as 'Add integration'
    in the UI does. `domain` is the integration name, e.g. 'wiz', 'met'.

    Returns the first step. `type` is one of:
    - `form`: answer with `continue_config_flow` and a `user_input` matching
      `data_schema` (`errors` holds validation messages from the last try)
    - `menu`: answer with `user_input={"next_step_id": <one of menu_options>}`
    - `create_entry`: done; `entry_id` is the new config entry
    - `abort`: stopped, `reason` says why (e.g. 'already_configured')
    - `external` / `progress`: needs a browser (OAuth) or is still working;
      OAuth integrations can't be finished through this API.
    """
    return await c(views.ConfigFlowListView, "post", body={"domain": domain})


@tool
async def continue_config_flow(
    c, flow_id: str, user_input: dict[str, Any] | None = None
) -> Any:
    """Submit `user_input` for the current step of a config flow and return
    the next step (same result shape as `start_config_flow`). Without
    `user_input`, returns the current step, e.g. to see the form of a
    discovered flow from `list_config_flows`."""
    return await c(
        views.ConfigFlowItemView,
        "post",
        flow_id=flow_id,
        body={"user_input": user_input},
    )


@tool
async def abort_config_flow(c, flow_id: str) -> Any:
    """Abort a config flow in progress."""
    return await c(views.ConfigFlowItemView, "delete", flow_id=flow_id)


# ---------------------------------------------------------------------------
# Diagnostics (repairs + notifications)
# ---------------------------------------------------------------------------


@tool
async def list_repairs(c) -> Any:
    """List open repair issues (the Repairs panel).

    Each item has domain, issue_id, resolved title/description, severity,
    whether it is fixable, the HA version it breaks in, a learn-more URL, plus
    the raw translation_key/placeholders/data for diagnosing problems.
    """
    return await c(views.RepairsView, "get")


@tool
async def list_notifications(c) -> Any:
    """List active persistent notifications (the bell menu).

    Each item has notification_id, title, message, and created_at. Many
    integrations post failures here, so it is a good first stop when
    diagnosing a problem.
    """
    return await c(views.NotificationsView, "get")


@tool
async def update_statistic_unit(
    c,
    statistic_id: str,
    unit_of_measurement: str,
    unit_class: str | None = None,
) -> Any:
    """Fix a statistics unit mismatch (the Repairs "Repareren" button).

    Rewrites the long-term statistics metadata unit for `statistic_id` WITHOUT
    deleting history, the same as Developer Tools > Statistics > Fix. Use when a
    repair reports the stored unit no longer matches the sensor (e.g. metadata
    says 'unitless' but the sensor now reports 'kWh').

    Args:
        statistic_id: e.g. 'sensor.hal_p1_meter_p1_teruglevering_vandaag'.
        unit_of_measurement: new normalized unit, e.g. 'kWh'.
        unit_class: unit conversion class, e.g. 'energy'. Strongly recommended;
            HA Core requires it from 2026.11. Omit only for a unitless statistic.
    """
    return await c(
        views.StatisticsMetadataView,
        "post",
        body={
            "statistic_id": statistic_id,
            "unit_of_measurement": unit_of_measurement,
            "unit_class": unit_class,
        },
    )


@tool
async def get_energy_prefs(c) -> Any:
    """Read the energy dashboard settings (Settings > Dashboards > Energy):
    `prefs` has `energy_sources` (meters, power sensors and costs per
    source), `device_consumption` and `device_consumption_water`.
    `validation` lists the problems the energy page shows, per source and
    device in the same order. `currency` is the currency of the prices;
    `solar_forecast_entries` are the config entries a solar source can use
    as `config_entry_solar_forecast`. `config_hash` identifies this version
    of the settings; pass it to `set_energy_prefs`. Read this before
    `set_energy_prefs`; its description lists every field."""
    return await c(views.EnergyPrefsView, "get")


@tool
async def set_energy_prefs(
    c,
    config_hash: str,
    energy_sources: list[dict[str, Any]] | None = None,
    device_consumption: list[dict[str, Any]] | None = None,
    device_consumption_water: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> Any:
    """Change the energy dashboard settings, entities and costs alike.
    Each list passed REPLACES that whole list, so start from
    `get_energy_prefs` and send the full edited list; lists left out stay
    as they are. Validated with Home Assistant's own schemas; `name`
    (display name) is optional everywhere.

    `config_hash` is the one `get_energy_prefs` returned: when the settings
    changed since, nothing is saved and the call fails (read again and redo
    the change). With `dry_run` the input is validated and the result shows
    the `changed` lists and the resulting `prefs` without saving; show that
    to the user before saving a change they did not spell out.

    `energy_sources` items, by `type`:
    - grid (one per connection; at least one meter or power sensor):
      `stat_energy_from` import meter, `stat_energy_to` export meter,
      `cost_adjustment_day` fixed cost per day (required, 0 for none).
      Import cost: `stat_cost` (a cost statistic) OR `entity_energy_price`
      (price entity) OR `number_energy_price` (fixed price per unit), the
      unused ones null. Export compensation likewise: `stat_compensation`,
      `entity_energy_price_export`, `number_energy_price_export`. Power:
      `stat_rate` or `power_config` (`stat_rate`, `stat_rate_inverted`, or
      `stat_rate_from` + `stat_rate_to`).
    - solar: `stat_energy_from`, `stat_rate`?, `config_entry_solar_forecast`
      (list of entry ids).
    - battery: `stat_energy_from` (discharged), `stat_energy_to` (charged),
      `stat_rate`?/`power_config`?, `stat_soc`? (charge %), `capacity`? (kWh).
    - gas and water: `stat_energy_from`, `stat_rate`?, and cost as for grid
      import (`stat_cost` / `entity_energy_price` / `number_energy_price`).
    - home (at most one): `stat_energy_to`, `stat_rate`?.
    Prices are per unit of the meter (kWh, m3) in `currency`. Prices can't
    be set on external statistics (ids with ':'); use a cost statistic.

    `device_consumption` / `device_consumption_water` items:
    `{"stat_consumption": "sensor.x", "stat_rate"?, "name"?,
    "included_in_stat"? (the stat of a parent device)}`.

    Meters must have long-term statistics (state_class total or
    total_increasing)."""
    return await c(
        views.EnergyPrefsView,
        "post",
        body={
            "config_hash": config_hash,
            "dry_run": dry_run,
            "energy_sources": energy_sources,
            "device_consumption": device_consumption,
            "device_consumption_water": device_consumption_water,
        },
    )


# ---------------------------------------------------------------------------
# HACS / Marketplace
# ---------------------------------------------------------------------------


@tool
async def hacs_search(
    c,
    query: str | None = None,
    category: str | None = None,
    installed: bool | None = None,
    sort: str | None = None,
    limit: int | None = None,
) -> Any:
    """Search the store: the Marketplace (Home Assistant 2026.11+) or, on
    older installs, HACS. `query` matches name, `owner/repo`,
    description and domain; `category` is e.g. 'integration', 'plugin'
    (dashboard cards), 'theme'; `installed` filters on installed state.
    `sort` is 'stars' (default, most first), 'last_updated' (most recent
    first) or 'name'. At most `limit` (default 25) results. Each item has
    `id`, `full_name`, `category`, `domain`, `installed_version`,
    `available_version`, `pending_upgrade` and `last_updated`. `downloads_allowed` says
    whether `hacs_download` is enabled."""
    return await c(
        views.HacsRepositoriesView,
        "get",
        query={
            "query": query,
            "category": category,
            "installed": installed,
            "sort": sort,
            "limit": limit,
        },
    )


@tool
async def hacs_download(
    c,
    repository: str,
    category: str | None = None,
    version: str | None = None,
) -> Any:
    """Install or update a repository through the Marketplace or HACS:
    `repository` is its `id` or `owner/repo`. A repository not in the store is added as a custom
    repository first, which needs `category`. `version` picks a release tag
    (default: latest).

    This installs third-party code with full Home Assistant rights. Only use
    it when the user asked for that repository; it fails unless 'Allow HACS
    downloads' is on in the integration options. The store carries it out as
    the user of your access token: in the Marketplace that user must have
    accepted its warning, and adding a custom repository needs GitHub
    connected there; an error that says so is for the user to settle in the
    Marketplace. When `restart_required`
    is true, restart Home Assistant (ask the user first), then add the
    integration with `start_config_flow` using its `domain`."""
    return await c(
        views.HacsDownloadView,
        "post",
        body={"repository": repository, "category": category, "version": version},
    )


@tool
async def hacs_remove(c, repository: str) -> Any:
    """Uninstall a repository downloaded through the Marketplace or HACS:
    `repository` is its
    `id` or `owner/repo`. Only use it when the user asked to remove that
    repository; it fails unless 'Allow HACS downloads' is on in the
    integration options. HACS itself and this integration can't be removed.

    For an integration, first remove its config entries with
    `remove_config_entry`; the Marketplace refuses otherwise, HACS leaves
    them behind. When
    `restart_required` is true, restart Home Assistant (ask the user first)."""
    return await c(views.HacsRemoveView, "post", body={"repository": repository})


# ---------------------------------------------------------------------------
# Matter
# ---------------------------------------------------------------------------


@tool
async def commission_matter(c, code: str, network_only: bool | None = None) -> Any:
    """Add a Matter device to Home Assistant with its pairing code, as the
    companion app's "Add Matter device" does. `code` is the 11-digit manual
    code (dashes allowed) or an `MT:` QR payload. For a device already in
    another ecosystem (Google Home, Apple Home, SmartThings), get a fresh
    code there via "Linked Matter apps & services" / "Turn on pairing mode";
    it expires after a few minutes. `network_only` (default true) means the
    device is already on the network; set false for a new device that must
    be set up over Bluetooth, which needs Bluetooth on the Matter server.
    Returns `node_id`; the device appears in `list_devices` with
    integration 'matter' shortly after. Takes up to a minute."""
    return await c(
        views.MatterCommissionView,
        "post",
        body={"code": code, "network_only": network_only},
    )


# ---------------------------------------------------------------------------
# Recorder-backed history
# ---------------------------------------------------------------------------


@tool
async def get_history(
    c,
    entity_id: str,
    hours: float | None = None,
    days: float | None = None,
    start: str | None = None,
    end: str | None = None,
    significant: bool = False,
    minimal: bool = True,
    no_attributes: bool = True,
) -> Any:
    """Fetch state-change history for one or more entities from HA's recorder.

    Default window: last 24 hours. Use this to diagnose flapping sensors
    (count of changes) or to inspect when an automation last ran.

    Args:
        entity_id: A single entity id, or comma-separated list for multiple
                   (e.g. 'sensor.x,sensor.y').
        hours: Window length in hours, ending at `end` (default now).
        days: Window length in days. Ignored if `hours` is set.
        start: ISO-format start datetime (e.g. '2026-05-22T00:00:00+00:00').
               Overrides `hours`/`days`.
        end: ISO-format end datetime (default: now).
        significant: If true, use `get_significant_states` (HA's filtered
                     view). Default false = every recorded state change.
        minimal: Smaller response shape (state + last_changed only).
                 Default true.
        no_attributes: Strip attributes from the response. Default true.

    Returns: {start, end, counts: {entity_id: n}, items: {entity_id: [...]}}.
    `counts` is the quickest way to see how often a sensor flipped.
    """
    return await c(
        views.HistoryView,
        "get",
        query={
            "entity_id": entity_id,
            "hours": hours,
            "days": days,
            "start": start,
            "end": end,
            "significant": significant,
            "minimal": minimal,
            "no_attributes": no_attributes,
        },
    )


@tool
async def list_statistics(
    c, search: str | None = None, statistic_type: str | None = None
) -> Any:
    """List the long-term statistics the recorder keeps: hourly aggregates
    that stay after the detailed state history is purged.

    `search` matches the statistic id (usually the entity id) or name;
    `statistic_type` is 'sum' (totals such as energy and gas) or 'mean'
    (measured values such as temperature). Each item has `statistic_id`,
    `unit`, `unit_class`, `source`, `has_sum` and `mean_type` (0 none,
    1 arithmetic, 2 circular).
    """
    return await c(
        views.StatisticsListView,
        "get",
        query={"search": search, "type": statistic_type},
    )


@tool
async def get_statistics(
    c,
    statistic_ids: list[str],
    period: str = "day",
    start: str | None = None,
    end: str | None = None,
    hours: float | None = None,
    days: float | None = None,
    types: list[str] | None = None,
    units: dict[str, Any] | None = None,
    max_rows: int = 2000,
) -> Any:
    """Fetch long-term statistics: how much energy or gas was used per day,
    month or year, or how a value developed.

    Args:
        statistic_ids: The ids (from `list_statistics`), e.g.
            ['sensor.p1_meter_energy_import'].
        period: Row size: '5minute', 'hour', 'day' (default), 'week',
            'month' or 'year'. '5minute' data only goes back about 10 days.
        start, end: The window, as ISO times. A time without an offset is
            local time and a date alone is local midnight ('2022-01-01').
            `start` is moved back to the start of its period, so that the
            first row is a whole day, month or year. `end` defaults to now.
        hours, days: The window as a length ending at `end`, instead of
            `start`.
        types: The values per row. Default ['change', 'sum', 'state'], for
            totals: `change` is the use within the row, `sum` the running
            total, `state` the meter reading. For measured values pass
            ['mean', 'min', 'max'].
        units: Convert to another unit, per unit class: {'energy': 'Wh'}.
        max_rows: The most rows in one answer, over all ids (default 2000).

    Returns {period, start, end, statistics: {statistic_id: {columns:
    ['start', ...types], rows: [[...], ...]}}, truncated}. `start` of a row
    is local time with its offset; numbers are rounded to 3 decimals. When
    `truncated` is true, call again with `start` set to `next_start` for the
    next page.
    """
    return await c(
        views.StatisticsDataView,
        "get",
        query={
            "statistic_ids": ",".join(stats_mod.as_list(statistic_ids, "statistic_ids")),
            "period": period,
            "start": start,
            "end": end,
            "hours": hours,
            "days": days,
            "types": ",".join(stats_mod.as_list(types, "types")) if types else None,
            "units": ",".join(f"{k}:{v}" for k, v in units.items()) if units else None,
            "max_rows": max_rows,
        },
    )


@tool
async def get_statistics_metadata(c, statistic_ids: list[str] | None = None) -> Any:
    """Return the metadata the recorder stores for statistics: the unit the
    values are stored in (`statistics_unit_of_measurement`), the unit they
    are shown in, `unit_class`, `source`, `has_sum` and `mean_type`.
    Without `statistic_ids`, of every statistic."""
    return await c(
        views.StatisticsMetadataView,
        "get",
        query={
            "statistic_ids": ",".join(stats_mod.as_list(statistic_ids, "statistic_ids"))
            if statistic_ids
            else None
        },
    )


@tool
async def adjust_statistic_sum(
    c,
    statistic_id: str,
    start_time: str,
    adjustment: float,
    unit: str | None = None,
    dry_run: bool = True,
) -> Any:
    """Correct a jump in the running total (`sum`) of a statistic, the same
    as Developer Tools > Statistics > the adjust-sum dialog.

    Adds `adjustment` to `sum` from the hour `start_time` on: that hour and
    every later hour shift by it, so the use (`change`) of that one hour
    changes by `adjustment` and all later hours keep theirs. Use it to take
    out a spike or a dip, e.g. after a meter was replaced or reset.

    Args:
        statistic_id: A statistic with a sum, e.g. 'sensor.gas_meter_gas'.
        start_time: ISO time of the start of an hour; without an offset it
            is local time.
        adjustment: The amount to add; negative to lower.
        unit: The unit of `adjustment` and of the answer; default the unit
            the statistic is shown in, which is what `get_statistics`
            returns. Home Assistant converts it to the stored unit.
        dry_run: True (default) changes nothing and returns the hours from
            two before to three after `start_time` as they are (`before`)
            and as they would become (`after_calculated`). Pass false to
            adjust; the answer then holds `before`, `after` as read back,
            and `applied`.

    This rewrites history and can't be undone other than by adjusting back
    with the opposite amount. Do a dry run first and show it to the user.
    """
    return await c(
        views.StatisticsAdjustView,
        "post",
        body={
            "statistic_id": statistic_id,
            "start_time": start_time,
            "adjustment": adjustment,
            "unit": unit,
            "dry_run": dry_run,
        },
    )


@tool
async def import_statistics(
    c, statistic_id: str, stats: list[dict[str, Any]], dry_run: bool = True
) -> Any:
    """Write hourly rows of an existing statistic: fill a gap or replace
    wrong hours. Hours that already have a row are overwritten.

    Args:
        statistic_id: An existing statistic; its metadata (source, unit,
            ...) is taken from the recorder and can't be changed here.
        stats: The rows, in order of time: [{"start": "2026-11-01T00:00:00",
            "state": 1234.5, "sum": 987.6}, ...]. `start` is the start of an
            hour (local time without an offset); `state` is the meter
            reading and `sum` the running total. `sum` may not decrease.
            The values must be in the unit the statistic is STORED in
            (`statistics_unit_of_measurement` of `get_statistics_metadata`),
            which can differ from the unit `get_statistics` shows; nothing
            is converted. The answer names the unit, and warns in
            `unit_note` when the two differ.
        dry_run: True (default) writes nothing and returns per hour the
            old and the new state and sum. Pass false to write.

    The hours after the import keep their `sum`. When the last imported
    `sum` does not connect to them, the answer holds a `warning` with the
    `difference`; correct the later hours with `adjust_statistic_sum`.
    This rewrites history; do a dry run first and show it to the user.
    """
    return await c(
        views.StatisticsImportView,
        "post",
        body={"statistic_id": statistic_id, "stats": stats, "dry_run": dry_run},
    )


@tool
async def list_traces(c, domain: str = "automation", item_id: str | None = None) -> Any:
    """List the stored runs of automations or scripts, newest first, to see
    whether and how one ran. Home Assistant keeps the last few runs of each.

    Args:
        domain: 'automation' (default) or 'script'.
        item_id: One automation or script: its id or its entity id. Without
            it, the runs of every automation or script.

    Each run has `run_id`, `item_id`, `timestamp` (start, finish), `trigger`,
    `state`, `script_execution` (e.g. 'finished', 'failed_conditions',
    'error', 'aborted'), `last_step` and, when it failed, `error`. Pass
    `item_id` and `run_id` to `get_trace` for the steps.
    """
    return await c(
        views.TraceListView, "get", query={"item_id": item_id}, domain=domain
    )


@tool
async def get_trace(c, item_id: str, run_id: str, domain: str = "automation") -> Any:
    """Return one run of an automation or script step by step, to find out
    why it did or did not do something.

    `trace` maps each step ('trigger/0', 'condition/0', 'action/0', ...) to
    what happened there: its `result` (a condition's outcome, the service
    called), `changed_variables` (the trigger data on the first step) and
    `error`. `config` is the config the run used. Take `item_id` and
    `run_id` from `list_traces`.
    """
    return await c(
        views.TraceItemView, "get", domain=domain, item_id=item_id, run_id=run_id
    )


@tool
async def get_logbook(
    c,
    entity_id: str | None = None,
    hours: float | None = None,
    days: float | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 200,
) -> Any:
    """Fetch the logbook: what changed and what caused it, in the order it
    happened. Use it to see what an automation did, or who or what switched
    a device.

    Args:
        entity_id: One entity id or several separated by commas; without
            it, everything.
        hours, days, start, end: The window, as for `get_history`;
            default the last 24 hours.
        limit: The most entries to return (default 200); the latest are
            kept, `total` tells how many there were.

    Each entry has `when`, `entity_id`, `state` or `message`, and where
    known what caused it (`context_entity_id`, `context_service`,
    `context_user_id`, ...).
    """
    return await c(
        views.LogbookView,
        "get",
        query={
            "entity_id": entity_id,
            "hours": hours,
            "days": days,
            "start": start,
            "end": end,
            "limit": limit,
        },
    )


@tool
async def get_system_log(
    c, level: str | None = None, search: str | None = None, limit: int = 50
) -> Any:
    """Fetch the errors and warnings Home Assistant logged since it started
    (Settings → System → Logs), newest first, to diagnose a failing
    integration or automation.

    Args:
        level: Only this level: 'ERROR', 'WARNING' or 'CRITICAL'.
        search: Only entries whose logger, message or traceback holds this.
        limit: The most entries to return (default 50).

    Each entry has `level`, `logger`, `message`, `exception` (the
    traceback), `source`, `count` (how often it repeated), `first_occurred`
    and `last_occurred`.
    """
    return await c(
        views.SystemLogView,
        "get",
        query={"level": level, "search": search, "limit": limit},
    )


# ---------------------------------------------------------------------------
# Core Home Assistant: states and services
# ---------------------------------------------------------------------------


@tool
async def get_state(c, entity_id: str) -> Any:
    """Get the current state of any HA entity."""
    state = c.hass.states.get(entity_id)
    if state is None:
        raise LookupError(f"entity not found: {entity_id}")
    return state.as_dict()


@tool
async def call_service(
    c,
    domain: str,
    service: str,
    data: dict[str, Any] | None = None,
) -> Any:
    """Call any Home Assistant service.

    Example: domain='light', service='turn_on',
    data={'entity_id': 'light.kitchen', 'brightness': 200}.

    Returns {"status": "ok", "response": ..., "operation_id": ...};
    `response` is the service's response data for services that return one,
    otherwise null. "ok" means Home Assistant accepted the call; pass
    `operation_id` to `get_operation_status` to see whether the entities
    followed. For several calls at once, use `bulk_control`.
    """
    operation, response = await operations_mod.call(
        c.hass, c.request["hass_user"].id, domain, service, data
    )
    if operation.error:
        raise RuntimeError(f"{operation.error} (operation_id {operation.id})")
    return {"status": "ok", "response": response, "operation_id": operation.id}


@tool
async def bulk_control(
    c, operations: list[dict[str, Any]], parallel: bool = True
) -> Any:
    """Make several service calls in one go, e.g. to control many devices.

    Args:
        operations: The calls, each {"domain": "light", "service":
            "turn_on", "data": {"entity_id": "light.kitchen",
            "brightness": 200}}; `data` is what `call_service` takes.
        parallel: Run them at the same time (default) or, when false, one
            after the other in the given order.

    One service call can already aim at many entities (`entity_id` as a
    list, or `area_id`); use this for calls that differ in service or data.
    A call that fails does not stop the others. Returns {"results": [...]}
    in the given order, each with `status` ("ok" or "failed"), `error`,
    `response` and an `operation_id` for `get_operation_status`.
    """
    results = await operations_mod.call_many(
        c.hass, c.request["hass_user"].id, operations, parallel
    )
    return {
        "status": "ok" if all(r["status"] == "ok" for r in results) else "failed",
        "results": results,
    }


@tool
async def get_operation_status(
    c, operation_ids: list[str], timeout_seconds: float = 8
) -> Any:
    """Tell whether service calls made with `call_service` or `bulk_control`
    took effect, by their `operation_id`.

    Per operation: `status` and `entities`, each with its current `state`
    and `changed` (its state was written since the call). The entities are
    those the call named in `entity_id`, plus those whose state it changed.
    - "completed": the call returned and every entity it named has changed.
    - "unconfirmed": the call returned, but a named entity has not changed.
      It may have been in that state already, or its device is slow or
      unreachable; check `state`.
    - "failed": the service raised an error (`error`).
    - "unknown": no such operation; they are kept in memory, not across a
      restart.

    `timeout_seconds` (default 8, at most 60) is how long to wait for
    unconfirmed entities to follow; 0 answers at once.
    """
    found = await asyncio.gather(
        *(
            operations_mod.status(c.hass, operation_id, timeout_seconds)
            for operation_id in operation_ids
        )
    )
    return {"operations": list(found)}


# ---------------------------------------------------------------------------
# JSON-RPC endpoint
# ---------------------------------------------------------------------------


async def _call_tool(request, params: dict[str, Any]) -> dict[str, Any]:
    fn = TOOLS[params["name"]][0]
    try:
        result = await fn(_Ctx(request), **(params.get("arguments") or {}))
    except Exception as e:  # noqa: BLE001 - tool errors go to the model, not the transport
        text, is_error = f"{type(e).__name__}: {e}", True
    else:
        text = result if isinstance(result, str) else json_dumps(result)
        is_error = False
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


class McpView(HomeAssistantView):
    url = "/api/automation_api/mcp"
    name = "api:automation_api:mcp"

    def _error(self, msg_id, code: int, message: str):
        return self.json(
            {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
        )

    async def post(self, request):
        views._require_admin(request)

        try:
            msg = await request.json()
        except Exception:
            return self._error(None, -32700, "Parse error")
        if not isinstance(msg, dict):
            return self._error(None, -32600, "Invalid Request")
        if "id" not in msg:  # notification (e.g. notifications/initialized)
            return web.Response(status=202)

        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            result = {
                "protocolVersion": requested
                if requested in PROTOCOL_VERSIONS
                else DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [definition for _, definition in TOOLS.values()]}
        elif method == "tools/call":
            if params.get("name") not in TOOLS:
                return self._error(
                    msg["id"], -32602, f"Unknown tool: {params.get('name')}"
                )
            result = await _call_tool(request, params)
        else:
            return self._error(msg["id"], -32601, f"Method not found: {method}")

        return self.json({"jsonrpc": "2.0", "id": msg["id"], "result": result})


def async_register_mcp(hass: HomeAssistant):
    hass.http.register_view(McpView)
