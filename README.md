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
dashboards, registries, integrations, the Marketplace (HACS), and history,
statistics, traces and logs.

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
- Storage-mode Lovelace dashboards: dashboards, views and cards, a patch
  for a change at any depth, and the resources of custom cards
- Entity, device and config-entry registries, including reload, enable,
  disable and remove
- Adding integrations through their config flows, including discovered ones
- Searching the Marketplace or HACS, and installing and removing
  repositories (opt-in)
- Adding Matter devices with a pairing code
- Reading repairs and persistent notifications; fixing a statistic's unit
- Reading and changing the energy dashboard settings
- What happened: state history with change counts, the traces of
  automation and script runs, the logbook and the error log
- Long-term statistics: read them per hour, day, month or year, and repair
  them (adjust a sum, import hours), with a dry run first
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
caller: automations, scripts, scenes, `input_*` helpers, creating and
changing dashboards, the store, statistics, traces and logs. For those the integration connects to Home
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
| `PATCH` | `/api/automation_api/lovelace/config/{url_path}` | Change any part of the config (`{patch: [...], config_hash?}`) |
| `POST` | `/api/automation_api/lovelace/view/{url_path}` | Append a view |
| `PUT` `DELETE` | `/api/automation_api/lovelace/view/{url_path}/{view_index}` | Replace or delete a view |
| `POST` | `/api/automation_api/lovelace/card/{url_path}/{view_index}` | Append a card |
| `PUT` `DELETE` | `/api/automation_api/lovelace/card/{url_path}/{view_index}/{card_index}` | Replace or delete a card |
| `GET` `POST` | `/api/automation_api/lovelace/resources` | List the resources of custom cards, or add one (`{url, type?}`) |
| `PATCH` `DELETE` | `/api/automation_api/lovelace/resources/{id}` | Change (`{url?, type?}`) or remove a resource |

**A patch** changes any part of a dashboard and leaves the rest as it is.
The card endpoints reach the cards directly in a view; a patch also reaches
cards inside a section or a stack, badges and titles. `patch` is a list of
JSON Patch operations, applied in order:

```bash
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  http://ha:8123/api/automation_api/lovelace/config/my-dashboard \
  -d '{"patch":[
        {"op":"test","path":"/views/0/path","value":"home"},
        {"op":"add","path":"/views/0/sections/1/cards/-","value":{"type":"tile","entity":"light.kitchen"}},
        {"op":"replace","path":"/views/0/title","value":"Living room"},
        {"op":"remove","path":"/views/2/cards/0"}]}'
# {"status":"ok","url_path":"my-dashboard","changed":true,"config_hash":"c7347dba8eb427b7"}
```

`op` is `add`, `remove`, `replace` or `test`. `path` is a JSON Pointer into
the config (`~1` for a `/` in a key, `~0` for a `~`). `add` inserts before
the given list index, or at the end with `-`, and sets a key of an object.
`test` stops the patch when the value there is not as given. Removing a list
entry shifts the ones after it. One failing operation leaves the dashboard
untouched.

**Changes made in between** are caught with a `config_hash`. Read the
config with `?hash=true` to get `{"config_hash", "config"}`, and send the
hash along: in the body of a `PATCH`, or as `?config_hash=` on a `PUT`. When
the dashboard has another config by now, nothing is saved and the answer is
a `409` with the current hash. Every save answers with the new hash.

**Resources** are the JavaScript and CSS files of custom cards (*Settings →
Dashboards → Resources*); `type` is `module` (the default), `js`, `css` or
`html`. They go through Lovelace's own `lovelace/resources/*` commands, sent
with the access token of the request. A card installed from the Marketplace
or HACS gets its resource from the store; leave those to it.

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

### History, statistics, traces and logs

Read-only endpoints for finding out what happened and why. Long-term
statistics have [their own section](#long-term-statistics).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/history?entity_id=&hours=` | Every recorded state change of one or more entities |
| `GET` | `/api/automation_api/traces/{domain}?item_id=` | The stored runs of automations or scripts, newest first; `domain` is `automation` or `script` |
| `GET` | `/api/automation_api/traces/{domain}/{item_id}/{run_id}` | One run, step by step |
| `GET` | `/api/automation_api/logbook?entity_id=&hours=&limit=` | The logbook: what changed and what caused it |
| `GET` | `/api/automation_api/system_log?level=&search=&limit=` | The errors and warnings of *Settings → System → Logs* |

All of them that take a time window take the same parameters; the default is
the last 24 hours.

| Parameter | Description |
|---|---|
| `hours`, `days` | Window length, ending at `end` (default: now) |
| `start`, `end` | Absolute ISO 8601 bounds; override `hours` and `days` |

Several entity ids go in one parameter, separated by commas.

**History** returns the recorder's state changes. `significant=true` uses
`get_significant_states` instead of every change, `minimal=false` adds
`last_updated`, and `no_attributes=false` keeps the attributes. The response
carries a `counts` map per entity, which shows flapping sensors at a glance:

```json
{
  "start": "2026-05-22T11:00:00+00:00",
  "end": "2026-05-23T11:00:00+00:00",
  "counts": {"sensor.outdoor_temperature": 47},
  "items": {"sensor.outdoor_temperature": [{"state": "...", "last_changed": "..."}]}
}
```

**Traces** show whether and how an automation or script ran. The list gives
each run's `run_id`, `trigger`, `script_execution` (`finished`,
`failed_conditions`, `error`, ...) and `last_step`; a single run gives every
step with its result, the trigger data and any error, as the trace view of
the editor does. `item_id` is the automation's id or its entity id. Home
Assistant keeps the last few runs of each automation.

**The logbook** lists what changed in order, with what caused it where Home
Assistant knows (`context_entity_id`, `context_service`, `context_user_id`).
`limit` (default 200) keeps the latest entries; `total` tells how many there
were.

**The system log** holds the errors and warnings since Home Assistant
started, newest first, each with its `logger`, `message`, traceback
(`exception`), `count` and when it first and last occurred. `level` and
`search` filter; `limit` defaults to 50.

Traces, the logbook and the system log are read with Home Assistant's own
WebSocket commands, sent with the access token of the request.

### Long-term statistics

The recorder keeps an aggregate per hour for every sensor with a state
class, long after the detailed history is purged: the data behind the
energy dashboard. These endpoints use the recorder's own WebSocket commands,
sent with the access token of the request.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/statistics?search=&type=` | List the statistics; `type` is `sum` (totals such as energy) or `mean` (measured values) |
| `GET` | `/api/automation_api/statistics/data?statistic_ids=&period=&start=&end=&types=` | Read statistics as a table per id |
| `GET` | `/api/automation_api/statistics/metadata?statistic_ids=` | The metadata the recorder stores, with the unit values are stored in |
| `POST` | `/api/automation_api/statistics/metadata` | Change the stored unit and keep the history (see [Repairs](#repairs-notifications-and-statistics)) |
| `POST` | `/api/automation_api/statistics/adjust_sum` | Shift the running total from an hour on; a dry run unless `"dry_run": false` |
| `POST` | `/api/automation_api/statistics/import` | Write hourly rows of an existing statistic; a dry run unless `"dry_run": false` |

#### Reading

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://ha:8123/api/automation_api/statistics/data?statistic_ids=sensor.gas_meter_gas&period=month&types=change&start=2026-01-01"
# {"period":"month","start":"2026-01-01T00:00:00+01:00","end":"...","truncated":false,
#  "statistics":{"sensor.gas_meter_gas":{"columns":["start","change"],
#    "rows":[["2026-01-01T00:00:00+01:00",142.318],["2026-02-01T00:00:00+01:00",118.904], ...]}}}
```

| Parameter | Description |
|---|---|
| `statistic_ids` | One or more ids, separated by commas |
| `period` | `5minute`, `hour`, `day` (default), `week`, `month` or `year`. `5minute` data only goes back about 10 days |
| `start`, `end` | ISO times; `end` defaults to now. `hours` or `days` give the window as a length instead |
| `types` | Default `change,sum,state`, for totals: `change` is the use within the row, `sum` the running total, `state` the meter reading. For measured values ask for `mean,min,max` |
| `units` | Convert, per unit class: `energy:Wh,volume:L` |
| `max_rows` | The most rows in one answer, over all ids (default 2000) |

- **Times are local.** A time without an offset is taken in Home
  Assistant's time zone, and a date alone (`2022-01-01`) is local midnight.
  Rows carry their offset (`+01:00`, `+02:00`). A day read per hour has 24
  rows, or 23 or 25 when the clocks change.
- **Whole periods.** `start` is moved back to the start of the period it
  lies in, so the first row is a whole day, month or year and not the rest
  of one. Without a window you get the last day (per 5 minutes or hour), 30
  days, 26 weeks, year or 5 years.
- **Compact.** Per statistic `columns` and `rows`, with numbers rounded to
  three decimals.
- **Paging.** An answer that would hold more than `max_rows` rows is cut
  after a whole period for every id, with `"truncated": true` and
  `next_start`. Ask again with `start` set to `next_start` for the next
  page.

#### Repairing

Both tools rewrite history, so both only describe what they would do until
the body holds `"dry_run": false`. They need an administrator, like every
endpoint.

**`POST /statistics/adjust_sum`** with `{statistic_id, start_time,
adjustment, unit?}` adds `adjustment` to the running total from the hour
`start_time` on. That hour and every later hour shift by it, so the use of
that one hour changes by `adjustment` and all later hours keep theirs. It
takes out a spike or a dip, for example after a meter was replaced. It is
what the adjust dialog of *Developer Tools → Statistics* does. `unit`
defaults to the unit the statistic is shown in. The answer holds the hours
from two before to three after `start_time`: `before`, and
`after_calculated` for a dry run or `after`, read back, with `applied`.

**`POST /statistics/import`** with `{statistic_id, stats: [{start, state?,
sum?}, ...]}` writes hourly rows, replacing the hours that already have
one. It fills a gap or replaces wrong hours.

- The statistic has to exist. Its metadata (source, unit, ...) is taken
  from the recorder and can't be changed here.
- `start` is the start of an hour, the rows are in order of time and `sum`
  does not decrease; anything else is refused.
- The values must be in the unit the statistic is **stored** in, which can
  differ from the unit it is shown in (a gas meter stored in ft³ and shown
  in m³). Nothing is converted. The answer names the unit and adds a
  `unit_note` when the two differ; read the current values in the stored
  unit with `units=volume:ft³`.
- The answer lists per hour the old and the new `state` and `sum`.
- The hours after the import keep their `sum`. When the last imported sum
  does not connect to them, the answer holds a `warning` with the
  `difference`, and the `adjust_sum` call that corrects the later hours.

There is no endpoint that deletes statistics.

## MCP server

The integration serves an MCP server over Streamable HTTP at
`/api/automation_api/mcp`. It offers 88 tools: one for every REST endpoint,
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
| Dashboards | Yes | No | Yes |
| Entity and device registry | Yes | No | Yes |
| Add integrations (config flows) | Yes | No | Yes |
| Add devices | Matter, with a pairing code | No | Matter, Z-Wave, Zigbee and Thread |
| HACS / Marketplace | Search, install and remove through either; changes off by default | No | Yes |
| Energy dashboard settings | Read, validate and change, costs included; dry run and stale-read check | No | Yes, with a dry run |
| History, statistics, traces and logs | Yes | No | Yes |
| Repair statistics (adjust a sum, import hours) | Yes | No | Yes |
| Repairs and notifications | Read both; fix a statistic's unit | No | Read repairs |
| Backups, add-ons, labels, zones, calendars, to-dos | No | No | Yes |
| Safeguards | Admin only, store changes opt-in, Home Assistant validates every change, action log | Exposed entities only | Read-only mode, per-tool switches, approval policies, edit backups |
| Tools | 88 | Depends on Assist | 87 |

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
