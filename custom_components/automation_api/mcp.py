"""MCP server (Streamable HTTP) served straight from the integration.

One endpoint, POST /api/automation_api/mcp, speaking stateless JSON-RPC:
initialize, ping, tools/list and tools/call. Auth is HA's own bearer token
(administrator), exactly like the REST endpoints.

Every tool dispatches in-process to the REST views in http.py, so validation,
logging and reloads live in one place.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.helpers.json import json_dumps

from . import http as views
from .const import LOG_FILE

SERVER_INFO = {"name": "ha-automation-api", "version": "1.2.2"}
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
    """Create or update an automation (written to automations.yaml + reloaded).

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
    """Delete an automation by id (removes it from automations.yaml)."""
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
    has performed via REST, WebSocket, MCP, or service calls.
    """
    # The REST view streams a FileResponse; read the file directly instead.
    path = Path(c.hass.config.path(LOG_FILE))
    return await c.hass.async_add_executor_job(path.read_text)


# ---------------------------------------------------------------------------
# Managed configuration (<config>/packages/automation_api.yaml)
# ---------------------------------------------------------------------------


@tool
async def get_managed_package(c) -> Any:
    """Return the full managed package file as JSON (helpers, templates, etc.)."""
    return await c(views.PackageView, "get")


@tool
async def overwrite_managed_package(c, content: dict[str, Any]) -> Any:
    """Overwrite the entire managed package file (expert / bulk migration use).

    Triggers homeassistant.reload_all afterwards. Prefer the targeted
    upsert_* tools for day-to-day changes.
    """
    return await c(views.PackageView, "put", body=content)


@tool
async def upsert_helper(c, domain: str, helper_id: str, config: dict[str, Any]) -> Any:
    """Create or update a helper (reloads the domain automatically).

    Args:
        domain: One of input_boolean / input_datetime / input_number /
                input_select / input_text / input_button.
        helper_id: Slug used as the helper id, e.g. 'moestuin_startdatum'.
        config: Full YAML config dict for the helper. Examples:
            input_boolean → {"name": "...", "initial": true, "icon": "mdi:bell"}
            input_datetime → {"name": "...", "has_date": true, "has_time": false}
            input_number  → {"name": "...", "min": 0, "max": 100, "step": 1}
    """
    return await c(
        views.HelperItemView, "put", body=config, domain=domain, helper_id=helper_id
    )


@tool
async def delete_helper(c, domain: str, helper_id: str) -> Any:
    """Delete a helper from the managed package and reload the domain."""
    return await c(views.HelperItemView, "delete", domain=domain, helper_id=helper_id)


@tool
async def list_helpers(c, domain: str) -> Any:
    """List helpers of the given domain in the managed package."""
    return await c(views.HelpersListView, "get", domain=domain)


@tool
async def get_helper(c, domain: str, helper_id: str) -> Any:
    """Return the config of a single helper in the managed package."""
    return await c(views.HelperItemView, "get", domain=domain, helper_id=helper_id)


@tool
async def upsert_template_entity(
    c, template_type: str, name: str, config: dict[str, Any]
) -> Any:
    """Create or update a template entity (reloads the template domain).

    Args:
        template_type: One of sensor / binary_sensor / switch / button /
                       number / select.
        name: Display name; also the unique key used for upsert.
        config: Template config (e.g. {"state": "{{ ... }}",
                "unit_of_measurement": "d", "icon": "mdi:sprout",
                "unique_id": "...", ...}).
    """
    return await c(
        views.TemplateItemView, "put", body=config, ttype=template_type, name=name
    )


@tool
async def delete_template_entity(c, template_type: str, name: str) -> Any:
    """Delete a template entity by type + name (reloads template domain)."""
    return await c(views.TemplateItemView, "delete", ttype=template_type, name=name)


@tool
async def list_template_entities(c, template_type: str) -> Any:
    """List all template entities of the given type in the managed package."""
    return await c(views.TemplateListView, "get", ttype=template_type)


@tool
async def get_template_entity(c, template_type: str, name: str) -> Any:
    """Return the config of a single template entity by type + name."""
    return await c(views.TemplateItemView, "get", ttype=template_type, name=name)


@tool
async def upsert_history_stats_sensor(c, name: str, config: dict[str, Any]) -> Any:
    """Create or update a history_stats sensor entry.

    A HA restart is required for it to take effect (response includes
    restart_required=true). Example config:
        {"entity_id": "binary_sensor.moestuin_regent",
         "state": "on", "type": "time",
         "end": "{{ now() }}", "duration": {"hours": 2}}
    """
    return await c(views.HistoryStatsItemView, "put", body=config, name=name)


@tool
async def delete_history_stats_sensor(c, name: str) -> Any:
    """Delete a history_stats sensor (HA restart required)."""
    return await c(views.HistoryStatsItemView, "delete", name=name)


@tool
async def list_history_stats_sensors(c) -> Any:
    """List all history_stats sensors in the managed package."""
    return await c(views.HistoryStatsListView, "get")


@tool
async def get_history_stats_sensor(c, name: str) -> Any:
    """Return the config of a single history_stats sensor."""
    return await c(views.HistoryStatsItemView, "get", name=name)


@tool
async def upsert_notify_group(
    c, name: str, services: list[str], extra: dict[str, Any] | None = None
) -> Any:
    """Create or update a notify platform:group entry.

    After writing, HA must be restarted before `notify.<name>` becomes
    available (notify does not support hot reload).

    Args:
        name: Group name; service becomes `notify.<name>`.
        services: List of notify services to aggregate, e.g.
                  ["mobile_app_sm_s911b", "mobile_app_s25"].
        extra: Optional extra top-level keys to include on the group entry.
    """
    body = {**(extra or {}), "services": services}
    return await c(views.NotifyGroupItemView, "put", body=body, name=name)


@tool
async def delete_notify_group(c, name: str) -> Any:
    """Delete a notify group (HA restart required)."""
    return await c(views.NotifyGroupItemView, "delete", name=name)


@tool
async def list_notify_groups(c) -> Any:
    """List all notify groups in the managed package."""
    return await c(views.NotifyGroupListView, "get")


@tool
async def get_notify_group(c, name: str) -> Any:
    """Return the config of a single notify group."""
    return await c(views.NotifyGroupItemView, "get", name=name)


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

    Use after creating/updating/deleting notify groups or history_stats
    sensors, which have no reload service.
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
    """Create a new storage-mode Lovelace dashboard.

    The dashboard is immediately visible under Settings → Dashboards and at
    `/lovelace-<url_path>`. It starts empty; add views and cards separately.

    Args:
        url_path: URL slug — e.g. 'moestuin' yields /lovelace-moestuin.
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
    """Update dashboard metadata (title, icon, show_in_sidebar, require_admin).

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

    Each item has `entry_id`, `domain`, `title`, `state`, `disabled_by`,
    `supports_unload`, `supports_remove_device`. Filter by `domain`
    (integration name like 'tuya' or 'wiz').
    """
    return await c(views.ConfigEntryListView, "get", query={"domain": domain})


@tool
async def get_config_entry(c, entry_id: str) -> Any:
    """Get full details for a single config entry."""
    return await c(views.ConfigEntryItemView, "get", entry_id=entry_id)


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

    Returns {"status": "ok", "response": ...}; `response` is the service's
    response data for services that return one, otherwise null.
    """
    services = c.hass.services
    wants_response = (
        services.supports_response(domain, service) is not SupportsResponse.NONE
    )
    response = await services.async_call(
        domain,
        service,
        data or {},
        blocking=True,
        context=Context(user_id=c.request["hass_user"].id),
        return_response=wants_response,
    )
    return {"status": "ok", "response": response}


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
