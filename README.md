<p align="center">
  <a href="https://github.com/aderik/ha-automation-api/releases"><img src="https://img.shields.io/github/v/release/aderik/ha-automation-api" alt="Release" /></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS custom repository" /></a>
  <a href="https://github.com/aderik/ha-automation-api/actions/workflows/validate.yml"><img src="https://github.com/aderik/ha-automation-api/actions/workflows/validate.yml/badge.svg" alt="Validate" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/aderik/ha-automation-api" alt="License" /></a>
</p>

# Automation API: MCP server and REST API for Home Assistant

A Home Assistant custom integration that lets AI agents (Claude, Cursor or any
other MCP client) and scripts (n8n, Make, CI pipelines) configure Home
Assistant. It exposes a REST API for what the built-in API leaves out:
automations, scripts, scenes, helpers, template entities, Lovelace
dashboards, registries, integrations, the Marketplace (HACS) and recorder
history.

It writes no configuration files of its own. Everything is handed to the
part of Home Assistant that owns it, the way the UI does: the config API of
the automation and script editors, the commands of the Helpers and
Dashboards pages, config flows and the Marketplace. What you make through
the API is validated by Home Assistant, live at once and editable in the UI.

The integration also serves a built-in [MCP server](#mcp-server) that exposes
every endpoint below as a tool for MCP-capable agents, with no separate
process to run.

## Features

- Create, edit, delete and trigger automations, and create, edit and delete
  scripts and scenes, saved exactly as their editors in the UI save them
- Control devices: any service, several calls at once, and a check on
  whether the entities followed
- Helpers (`input_*`), template entities and `history_stats` sensors as
  ordinary UI helpers
- Storage-mode Lovelace dashboards: dashboards, views and cards
- Entity, device and config-entry registries, including reload, enable,
  disable and remove
- Adding integrations through their config flows, including discovered ones
- Searching the Marketplace or HACS, and installing and removing
  repositories (opt-in)
- Adding Matter devices with a pairing code
- Reading repairs and persistent notifications; fixing a statistic's unit
- Reading and changing the energy dashboard settings
- Recorder history with per-entity change counts
- Built-in MCP server (Streamable HTTP) with a tool for every endpoint
- Authenticated with Home Assistant's own access tokens (administrator)
- No configuration files written: every change goes through Home
  Assistant's own APIs
- Installable through the Marketplace or HACS

## Installation

1. In the Marketplace (Home Assistant 2026.11 and later) or HACS, add this
   repository as a custom repository of type *Integration*.
2. Install it and restart Home Assistant.
3. Go to *Settings → Devices & services → Add integration* and choose
   **Automation API**. There is nothing to configure. The only option,
   *Allow HACS / Marketplace downloads* under *Configure*, is off by default
   (see [HACS / Marketplace](#hacs--marketplace)).
4. Create a long-lived access token for an **administrator** account
   (profile → *Security*).

Developed and tested against Home Assistant 2026.11. Nothing has to be added
to `configuration.yaml`.

## Authentication

Every endpoint uses Home Assistant's own authentication, the same mechanism as
`/api/states`:

```
Authorization: Bearer <long-lived access token>
```

The token must belong to an administrator; other tokens receive `401`.

The header does more than open the door. Several endpoints act as the
caller: automations, scripts, `input_*` helpers, creating and changing
dashboards, and the store. For those the integration connects to Home
Assistant's own API with the token of the request and passes the request on,
so Home Assistant carries it out as that user, with its own validation and
permission checks. A request without `Authorization: Bearer` is refused
there with a `400`.

## REST API

All paths are relative to `http://<home-assistant>:8123`.

### Automations

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/automations` | List automations |
| `GET` | `/api/automation_api/automations?id=<id>` | Get one automation (live state) |
| `POST` | `/api/automation_api/automations` | Create or update (`{id, name, trigger, action, condition?, description?, mode?}`) |
| `DELETE` | `/api/automation_api/automations?id=<id>` | Delete; `404` if there is none |
| `POST` | `/api/automation_api/trigger` | Run the actions now, skipping conditions (`{"id": "<id>"}`) |
| `GET` | `/api/automation_api/automations_yaml?id=<id>` | The stored config, read from `automations.yaml`; omit `id` for all of them |
| `GET` | `/api/automation_api/areas` | List areas |
| `GET` | `/api/automation_api/entities?domain=&area=&search=` | List entities with optional filters |
| `GET` | `/api/automation_api/log` | The integration's own action log |

Examples:

```bash
curl -H "Authorization: Bearer $TOKEN" http://ha:8123/api/automation_api/automations

curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://ha:8123/api/automation_api/automations \
  -d '{"id":"example","name":"Example","trigger":[],"action":[]}'

curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://ha:8123/api/automation_api/trigger -d '{"id":"example"}'
```

Automations are saved through the config API of Home Assistant's automation
editor, with the access token of the request: Home Assistant validates the
config, stores it, reloads that automation and, on delete, removes its
entity. An invalid config is answered with `400` and Home Assistant's
message; nothing is stored. When the answer comes, the automation is loaded
and can be triggered.

`trigger`, `condition` and `action` take what the automation editor's YAML
mode takes. Home Assistant stores them under its current names (`triggers`,
`conditions`, `actions`).

### Scripts

| Method | Path | Description |
|---|---|---|
| `GET` `PUT` `DELETE` | `/api/automation_api/scripts/{id}` | Read, create or replace (body: the script config) or delete a script |

`{id}` becomes the entity id `script.<id>`. The body is what the script
editor stores. Scripts go through the config API of the script editor, like
automations, and are loaded when the answer comes.

```bash
curl -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://ha:8123/api/automation_api/scripts/water_given \
  -d '{"alias":"Water given","mode":"single","sequence":[
        {"action":"input_datetime.set_datetime",
         "target":{"entity_id":"input_datetime.last_watered"},
         "data":{"timestamp":"{{ now().timestamp() }}"}}]}'
```

### Scenes

| Method | Path | Description |
|---|---|---|
| `GET` `PUT` `DELETE` | `/api/automation_api/scenes/{id}` | Read, create or replace (body: the scene config) or delete a scene |

The body is what the scene editor stores: `{"name": "Movie night",
"entities": {"light.tv_lamp": {"state": "on", "brightness": 80},
"cover.blinds": "closed"}, "icon": "mdi:movie"}`. `{id}` is the scene's id,
any stable string; Home Assistant derives the entity id from `name`, and the
answer to a `PUT` holds it. Scenes go through the config API of the scene
editor, like scripts. Activate one with the `scene.turn_on` service.

### Helpers

Helpers are ordinary UI helpers, as made under *Settings → Devices & services
→ Helpers*: live at once, without a restart, and editable in the UI.

| Method | Path | Description |
|---|---|---|
| `GET` `POST` | `/api/automation_api/helpers/{domain}` | List the helpers of a domain, or create one (body: full config with `name`) |
| `GET` `PUT` `DELETE` | `/api/automation_api/helpers/{domain}/{id}` | Read, replace the config of, or delete a helper |
| `GET` `POST` | `/api/automation_api/template/{type}` | List the template entities of a type, or create one (body: the fields of the helper's form, with `name`) |
| `GET` `PUT` `DELETE` | `/api/automation_api/template/{type}/{entry_id}` | Read, change the options of, or delete a template entity |
| `GET` `POST` | `/api/automation_api/history_stats` | List `history_stats` sensors, or create one |
| `GET` `PUT` `DELETE` | `/api/automation_api/history_stats/{entry_id}` | Read, change the options of, or delete a `history_stats` sensor |

#### `input_*` helpers

`input_boolean`, `input_button`, `input_datetime`, `input_number`,
`input_select` and `input_text` helpers are created, changed and deleted
with the WebSocket commands of their integration, sent with the access token
of the request.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://ha:8123/api/automation_api/helpers/input_number \
  -d '{"name":"Storm gust","min":20,"max":80,"step":1,"unit_of_measurement":"km/h"}'
# {"status":"ok","domain":"input_number","helper":{"id":"storm_gust",
#  "entity_id":"input_number.storm_gust","name":"Storm gust","min":20.0, ...}}
```

Home Assistant derives the id of a new helper, and its entity id, from
`name`; the answer holds both. `PUT` replaces the whole config of an
existing helper, so send every field, `name` included; a config read with
`GET` can be sent back as it came.

#### Template entities and `history_stats` sensors

These are config entries, made with the config flow of their integration and
changed with its options flow, as the UI does. They are addressed by
`entry_id`; the answer to a create holds it, with the entities made.

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://ha:8123/api/automation_api/template/sensor \
  -d '{"name":"Days since watering","unit_of_measurement":"d",
       "state":"{{ ((now() - states(\"input_datetime.last_watered\") | as_datetime | as_local).days) }}"}'
# {"status":"ok","entry_id":"01M89...","title":"Days since watering",
#  "config":{"template_type":"sensor", ...},"entities":["sensor.days_since_watering"]}

curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://ha:8123/api/automation_api/history_stats \
  -d '{"name":"Rain last 2h","entity_id":"binary_sensor.raining","state":["on"],
       "type":"time","end":"{{ now() }}","duration":{"hours":2}}'
```

- **Fields.** The body holds the fields of the helper's form. For a template
  entity those depend on the type: a sensor takes `name`, `state`,
  `unit_of_measurement`, `device_class`, `state_class` and `device_id`, and
  `additional_options` with `availability`. A `history_stats` sensor takes
  `name`, `entity_id`, `state` (a list), `type` (`time`, `ratio` or `count`),
  exactly two of `start`, `end` and `duration`, and `state_class`.
- **Types.** Any type the Template helper offers works: `sensor`,
  `binary_sensor`, `switch`, `button`, `number`, `select` and more.
- **Limits.** Only what the form offers is accepted, so a template entity
  has no attributes, icon template or trigger. A field the form does not
  have is refused and nothing is created. Set a fixed icon on the entity
  with `PATCH /entity_registry/{entity_id}`.
- **Changing.** `PUT` replaces the options the form holds: optional fields
  left out are cleared, and `name` can't be changed there.

#### What is not covered

Helpers, template entities and sensors defined in YAML are not listed and
can't be changed here.

Other helpers that are config entries, such as groups (a notify group too)
and `statistics` sensors, can be made with the [config flow
endpoints](#adding-integrations-config-flows). A notify group made that way
groups notify entities and passes on a message and a title only. For a
group that also passes `data` on (channels, tags, actions), make a
[script](#scripts) with `fields` that calls each notify service.

### Reload and restart

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/automation_api/reload` | `{"domains": [...]}` reloads the YAML of those domains; an empty body calls `homeassistant.reload_all` |
| `POST` | `/api/automation_api/restart` | Restart Home Assistant |

Nothing made through this API needs either. They are there for YAML you
edit yourself and for integrations installed from the store, which need a
restart.

### Lovelace dashboards

Only storage-mode dashboards can be written; YAML-mode dashboards are
read-only and writes return `400`. Use `default` as `{url_path}` for the
Overview dashboard. After every save, Home Assistant fires `lovelace_updated`.

Lovelace's storage files are never touched. Views and cards are saved
through the dashboard object Lovelace keeps. Creating, updating and deleting
a dashboard goes through Lovelace's own `lovelace/dashboards/*` WebSocket
commands, sent with the access token of the request, as *Settings →
Dashboards* does. So a new dashboard works at once,
without a restart, and Lovelace's rules apply: `url_path` needs a hyphen
(`my-garden`) and may not be in use, and only `title`, `icon`,
`show_in_sidebar` and `require_admin` can be updated.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/lovelace/dashboards` | List dashboards |
| `POST` | `/api/automation_api/lovelace/dashboards` | Create (`{url_path, title, icon?, show_in_sidebar?, require_admin?}`) |
| `PATCH` `DELETE` | `/api/automation_api/lovelace/dashboards/{url_path}` | Update metadata or delete |
| `GET` `PUT` | `/api/automation_api/lovelace/config/{url_path}` | Read or overwrite the full config (`{views: [...]}`) |
| `POST` | `/api/automation_api/lovelace/view/{url_path}` | Append a view |
| `PUT` `DELETE` | `/api/automation_api/lovelace/view/{url_path}/{view_index}` | Replace or delete a view |
| `POST` | `/api/automation_api/lovelace/card/{url_path}/{view_index}` | Append a card |
| `PUT` `DELETE` | `/api/automation_api/lovelace/card/{url_path}/{view_index}/{card_index}` | Replace or delete a card |

### Registries

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/entity_registry?domain=&platform=&device_id=&area_id=&config_entry_id=&disabled=` | List registry entities |
| `GET` `PATCH` `DELETE` | `/api/automation_api/entity_registry/{entity_id}` | Read, update (`name`, `icon`, `area_id`, `new_entity_id`, `disabled_by`, `hidden_by`) or remove |
| `GET` | `/api/automation_api/device_registry?area_id=&manufacturer=&model=&integration=&config_entry_id=&disabled=` | List devices |
| `GET` `PATCH` `DELETE` | `/api/automation_api/device_registry/{device_id}` | Read, update (`name_by_user`, `area_id`, `disabled_by`) or remove with its entities |
| `GET` | `/api/automation_api/config_entries?domain=` | List integrations, with `reason` when setup failed or is retrying |
| `GET` `PATCH` `DELETE` | `/api/automation_api/config_entries/{entry_id}` | Read, change the device address (`{"host": "192.168.1.20"}`, reloads the entry), or remove with all its devices and entities |
| `POST` | `/api/automation_api/config_entries/{entry_id}/reload` | Reload an integration |
| `POST` | `/api/automation_api/config_entries/{entry_id}/disable` | Disable an integration |
| `POST` | `/api/automation_api/config_entries/{entry_id}/enable` | Enable an integration |

Config entries of local integrations show their device address as `host`.
When a device gets a new DHCP address, `PATCH` the entry with the new one
instead of removing and re-adding the integration; that keeps its devices,
entities, areas and history. It works for every integration that stores
`host` or `ip_address`, including those without a reconfigure flow.

Removing a registry entity while its integration is still active lets the
integration re-add it on the next reload; remove the device or the config
entry instead.

### Adding integrations (config flows)

Adds an integration the way *Add integration* in the UI does, step by step.
Every response is a flow step: `form` (answer with `{"user_input": {...}}`
matching `data_schema`), `menu` (`{"user_input": {"next_step_id": ...}}`),
`create_entry` (done, carries `entry_id`) or `abort` (`reason`). OAuth
integrations need a browser and can't be finished through the API. Helpers
that are config entries, such as groups and `statistics` sensors, are made
the same way (`{"domain": "group"}`, `{"domain": "statistics"}`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/config_flows` | Flows in progress, including discovered integrations |
| `POST` | `/api/automation_api/config_flows` | Start a flow (`{domain}`) |
| `POST` | `/api/automation_api/config_flows/{flow_id}` | Submit `{user_input}`; without a body, return the current step |
| `DELETE` | `/api/automation_api/config_flows/{flow_id}` | Abort a flow |

### HACS / Marketplace

Search the store and install, update or remove repositories. Home Assistant
2026.11 took HACS into core as the **Marketplace** integration; on older
installs the HACS custom integration is used. The Marketplace wins when both
are set up; the endpoints and MCP tools (`hacs_*`) are the same for both.

The integration is a client of the store, like the store's own panel: it
connects to Home Assistant's WebSocket API with the access token of the
request and sends the store's commands (`marketplace/repository/install`,
...; tested against the Marketplace of 2026.11, and written for HACS 2.0.5).
The store carries out every request itself, as the user of that token. So
the token has to be sent as `Authorization: Bearer`, and in the Marketplace
that user must have accepted its first-run warning; adding a custom
repository also needs GitHub connected there. Its refusals come back as the
`error` of a `400`.

Downloading installs third-party code that runs with full Home Assistant
rights, so on top of that downloads and removals are **off by default**:
enable *Allow HACS / Marketplace downloads* under *Settings → Devices &
services → Automation API → Configure*. Searching works either way.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/hacs/repositories?query=&category=&installed=&sort=&limit=` | Search the store; `sort` is `stars` (default), `last_updated` or `name`; 25 results by default |
| `POST` | `/api/automation_api/hacs/download` | Install or update (`{repository, category?, version?}`) |
| `POST` | `/api/automation_api/hacs/remove` | Uninstall (`{repository}`); HACS itself and this integration are refused |

`repository` is a store id or `owner/repo`. A repository that isn't in the
store is added as a custom repository first, which needs `category`
(`integration`, `plugin`, `theme`, ...). A downloaded integration needs a
restart (`restart_required: true`); then add it with a config flow. Before
removing an integration, remove its config entries: the Marketplace refuses
while they exist, HACS leaves them behind.

### Matter

`POST /api/automation_api/matter/commission` adds a Matter device with its
pairing code (`{code, network_only?}`), like the companion app's *Add Matter
device*. `code` is the 11-digit manual code (dashes allowed) or an `MT:` QR
payload. For a device that is already in Google Home, Apple Home or
SmartThings, generate a fresh code there (*Linked Matter apps & services*).
`network_only` defaults to `true` (device already on the network); `false`
sets up a new device over Bluetooth. Returns the new `node_id`.

### Repairs, notifications and statistics

`GET /api/automation_api/repairs` lists the open issues of the Repairs panel,
with `title` and `description` translated into Home Assistant's language and
their placeholders filled in, plus `severity`, `is_fixable`,
`breaks_in_ha_version`, `learn_more_url` and the raw `translation_key`,
`translation_placeholders` and `data`.

`GET /api/automation_api/notifications` lists the active persistent
notifications (`notification_id`, `title`, `message`, `created_at`). Many
integrations report failures there.

`POST /api/automation_api/statistics/metadata` with
`{statistic_id, unit_of_measurement, unit_class?}` changes the unit stored in
the long-term statistics metadata and keeps the history. It is what *Fix* in
Developer Tools > Statistics does (the `recorder/update_statistics_metadata`
WebSocket command), for example when a sensor now reports `kWh` and its old
statistics are unitless. Pass `unit_class` (such as `energy`); Home Assistant
requires it from 2026.11.

### Energy

The settings of Settings > Dashboards > Energy, read and written through Home
Assistant's energy manager as the energy page does (the `energy/get_prefs`,
`energy/validate` and `energy/save_prefs` WebSocket commands).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/energy` | `prefs` (`energy_sources` with their meters and costs, `device_consumption`, `device_consumption_water`), `config_hash`, `validation` (the problems the energy page shows), `currency` and `solar_forecast_entries` |
| `POST` | `/api/automation_api/energy` | Replace one or more of those lists; optional `config_hash` and `dry_run` |

Costs live in the sources: a fixed price (`number_energy_price`), a price
entity (`entity_energy_price`) or a cost statistic (`stat_cost`) for import,
gas and water; the `_export` variants and `stat_compensation` for grid export;
`cost_adjustment_day` for a fixed daily cost. The `set_energy_prefs` tool
description lists every field.

Each list passed replaces the stored one; lists left out stay as they are, so
read first and send the whole edited list. Pass the `config_hash` of that read:
when the settings changed in between, nothing is saved and the answer is a 409
with the current hash. `"dry_run": true` validates and returns the `changed`
lists and the resulting `prefs` without saving. The MCP tool requires
`config_hash`. Input is checked with Home
Assistant's own schemas, and invalid input is a 400.

### History

`GET /api/automation_api/history?entity_id=sensor.x&hours=24` returns the
recorder's state changes for one or more entities (`entity_id=sensor.x,sensor.y`).

| Parameter | Description |
|---|---|
| `hours`, `days` | Window length, ending at `end` (default: now) |
| `start`, `end` | Absolute ISO 8601 bounds; override `hours` and `days` |
| `significant=true` | Use `get_significant_states` instead of every change |
| `minimal=false` | Include `last_updated` next to `last_changed` |
| `no_attributes=false` | Keep attributes in the payload |

The response carries a `counts` map per entity, which shows flapping sensors
at a glance:

```json
{
  "start": "2026-05-22T11:00:00+00:00",
  "end": "2026-05-23T11:00:00+00:00",
  "counts": {"sensor.outdoor_temperature": 47},
  "items": {"sensor.outdoor_temperature": [{"state": "...", "last_changed": "..."}]}
}
```

## MCP server

The integration serves an MCP server over Streamable HTTP at
`/api/automation_api/mcp`. It offers 74 tools: one for every REST endpoint,
plus four for controlling devices. It uses the same administrator bearer
token. A tool runs its REST endpoint in-process, so both behave the same.

The four tools without a REST endpoint:

| Tool | What it does |
|---|---|
| `get_state` | The current state and attributes of an entity |
| `call_service` | Call any Home Assistant service; the answer holds an `operation_id` |
| `bulk_control` | Several service calls in one go, at the same time or in order; a failing call does not stop the others, and each gets its own `operation_id` |
| `get_operation_status` | Whether those calls took effect: per operation `completed`, `unconfirmed` or `failed`, with the state of each entity the call named or changed. It can wait a few seconds for slow devices |

A service call that returns without an error only means Home Assistant
handed it to the integration. `get_operation_status` looks at the entities
afterwards: it reports the ones the call named in `entity_id` and the ones
whose state it changed, found through the context Home Assistant attaches to
the call. Operations are kept in memory, the latest 500, until a restart.

Claude Code:

```
claude mcp add --transport http ha-automation \
  http://<home-assistant>:8123/api/automation_api/mcp \
  --header "Authorization: Bearer <long-lived access token>"
```

Any other client that supports Streamable HTTP with a custom header works the
same way. The server is stateless and answers with plain JSON; it does not
open an SSE stream.

This replaces the standalone
[ha-automation-mcp](https://github.com/aderik/ha-automation-mcp) server.

### Compared to other MCP servers

Home Assistant ships its own
[MCP Server integration](https://www.home-assistant.io/integrations/mcp_server/),
and the community maintains [ha-mcp](https://github.com/homeassistant-ai/ha-mcp).

| | Automation API | Built-in MCP Server | ha-mcp |
|---|---|---|---|
| Purpose | Configure HA from agents and scripts | Voice-style control through Assist | Configure, build and debug HA |
| Runs | Inside HA (custom integration) | Inside HA (core integration) | Inside HA (custom component), or as add-on, Docker or `uvx` |
| Authentication | Admin access token | OAuth or token | Admin token or its own account; secret URL or HA sign-in for clients |
| Entity scope | Everything | Only entities exposed to Assist | Everything |
| Control devices | Yes | Assist intents | Yes |
| REST API besides MCP | Yes | No | No |
| How changes are made | Through Home Assistant's own APIs, as the caller; no files written | Assist intents | Through Home Assistant's APIs; file and YAML tools are opt-in |
| Automations | Yes | No | Yes |
| Scripts and scenes | Yes | No | Yes |
| Blueprints | No | No | Yes |
| `input_*` helpers | Yes, as UI helpers | No | Yes, as UI helpers |
| Template entities, `history_stats` | Yes, as UI helpers | No | Template helpers |
| Other helpers (groups, `statistics`, ...) | Through the config flow tools | No | Yes |
| Dashboards | Dashboards, views and single cards | No | Yes |
| Entity and device registry | Yes | No | Yes |
| Add integrations (config flows) | Yes | No | Yes |
| Add devices | Matter, with a pairing code | No | Matter, Z-Wave, Zigbee and Thread |
| HACS / Marketplace | Search, install and remove through either; changes off by default | No | Yes |
| Energy dashboard settings | Read, validate and change, costs included; dry run and stale-read check | No | Yes, with a dry run |
| History | Yes, with change counts | No | Yes, plus statistics, traces and logs |
| Repairs and notifications | Read both; fix a statistic's unit | No | Read repairs, plus logs |
| Backups, add-ons, labels, zones, calendars, to-dos | No | No | Yes |
| Safeguards | Admin only, store changes opt-in, Home Assistant validates every change, action log | Exposed entities only | Read-only mode, per-tool switches, approval policies, edit backups |
| Tools | 74 | Depends on Assist | 87 |

Use the built-in server to control exposed devices, ha-mcp for the broadest
toolset, and Automation API when you also want a plain REST API (n8n, Make,
CI) next to MCP.

## Upgrading

### From 1.x to 2.0

Version 2.0.0 no longer writes YAML. If you used an earlier version:

- **The package file** `<config>/packages/automation_api.yaml` is no longer
  read or written. Home Assistant keeps loading it, so what is in it keeps
  working; it is yours to edit or to move into the UI. The endpoints and
  tools for the whole package (`/package`, `get_managed_package`,
  `overwrite_managed_package`) and for notify groups (`/notify_group`,
  `*_notify_group`) are gone.
- **Helpers** are now UI helpers: create with `POST
  /helpers/{domain}`, and the id comes from `name`. `PUT
  /helpers/{domain}/{id}` only changes an existing UI helper.
- **Template entities and `history_stats` sensors** are addressed by
  `entry_id` instead of name, and take the fields of the helper's form.
- **The services** `automation_api.create_automation`,
  `delete_automation` and `test_automation`, and the WebSocket commands
  `automation_api/create`, `delete` and `test`, are gone. Use the REST
  endpoints or the MCP tools; to run an automation from a script, use
  `automation.trigger`.
- **Deleting an automation** that does not exist now answers `404`.
- **Moving what the package file holds into the UI** keeps the entity ids
  when you take an item out of the file, reload its domain and then create
  it again here. For an `input_*` helper, create it with its old id as
  `name` and give it its real name with a `PUT`. A template or `statistics`
  entity that had a `unique_id` leaves a registry entry behind that holds
  its entity id; remove that entry first. A `statistics` helper takes the
  name of its source sensor's device into its entity id; give it its old id
  back with `PATCH /entity_registry/{entity_id}` (`new_entity_id`).

### From 0.8.x or earlier

The `X-API-KEY` header and the generated API key no longer exist. Send a
bearer token instead. The integration does not need to be re-added.

## Notes

The integration writes one file: its own action log,
`<config>/automation_api.log`, with a line for every change it was asked to
make (`GET /api/automation_api/log`). It reads `automations.yaml` for the
stored-config endpoint. It writes no configuration and keeps no state of its
own; everything it makes belongs to the Home Assistant integration that
holds it and stays when this integration is removed.

## License

[MIT](LICENSE)
