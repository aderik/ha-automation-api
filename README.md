<p align="center">
  <a href="https://github.com/aderik/ha-automation-api/releases"><img src="https://img.shields.io/github/v/release/aderik/ha-automation-api" alt="Release" /></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS custom repository" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/aderik/ha-automation-api" alt="License" /></a>
</p>

# Automation API

A Home Assistant custom integration that exposes a REST and WebSocket API for
managing configuration that the built-in API leaves out: automations,
helpers, template entities, Lovelace dashboards, registries and recorder
history. It is built for machine clients: AI agents, n8n or Make, custom
dashboards and CI pipelines.

The integration also serves a built-in [MCP server](#mcp-server) that exposes
every endpoint below as a tool for MCP-capable agents, with no separate
process to run.

## Features

- Full CRUD, list and trigger for automations, written to `automations.yaml`
  exactly as the Home Assistant UI does
- Helpers (`input_*`), template entities, `history_stats` sensors and notify
  groups, managed in a single package file
- Storage-mode Lovelace dashboards: dashboards, views and cards
- Entity, device and config-entry registries, including reload, enable,
  disable and remove
- Recorder history with per-entity change counts
- Built-in MCP server (Streamable HTTP) with a tool for every endpoint
- Authenticated with Home Assistant's own long-lived access tokens
- Installable through HACS

## Installation

1. In HACS, add this repository as a custom repository of type *Integration*.
2. Install it and restart Home Assistant.
3. Go to *Settings → Devices & services → Add integration* and choose
   **Automation API**. There is nothing to configure.
4. Create a long-lived access token for an **administrator** account
   (profile → *Security*).

## Authentication

Every endpoint uses Home Assistant's own authentication, the same mechanism as
`/api/states`:

```
Authorization: Bearer <long-lived access token>
```

The token must belong to an administrator; other tokens receive `401`.

**Upgrading from 0.8.x or earlier:** the `X-API-KEY` header and the generated
API key no longer exist. Send a bearer token instead. The integration does not
need to be re-added.

## REST API

All paths are relative to `http://<home-assistant>:8123`.

### Automations

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/automations` | List automations |
| `GET` | `/api/automation_api/automations?id=<id>` | Get one automation (live state) |
| `POST` | `/api/automation_api/automations` | Create or update (`{id, name, trigger, action, condition?, description?, mode?}`) |
| `DELETE` | `/api/automation_api/automations?id=<id>` | Delete |
| `POST` | `/api/automation_api/trigger` | Run the actions now, skipping conditions (`{"id": "<id>"}`) |
| `GET` | `/api/automation_api/automations_yaml?id=<id>` | Raw YAML; omit `id` for the whole file |
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

### Managed configuration (packages)

Helpers, template entities, `history_stats` sensors and notify groups live in
one package file, `<config>/packages/automation_api.yaml`. This needs a
one-time addition to `configuration.yaml` and one restart:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

After that, each endpoint reloads the affected domain itself. The package file
is owned by the integration; edit it through the API, not by hand.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/helpers/{domain}` | List helpers of a domain |
| `GET` `PUT` `DELETE` | `/api/automation_api/helpers/{domain}/{id}` | Read, upsert (body: full config) or delete a helper |
| `GET` | `/api/automation_api/template/{type}` | List template entities of a type |
| `GET` `PUT` `DELETE` | `/api/automation_api/template/{type}/{name}` | Read, upsert or delete a template entity |
| `GET` | `/api/automation_api/history_stats` | List `history_stats` sensors |
| `GET` `PUT` `DELETE` | `/api/automation_api/history_stats/{name}` | Read, upsert or delete; takes effect after a restart |
| `GET` | `/api/automation_api/notify_group` | List notify groups |
| `GET` `PUT` `DELETE` | `/api/automation_api/notify_group/{name}` | Read, upsert (`{"services": [...]}`) or delete; takes effect after a restart |
| `GET` `PUT` | `/api/automation_api/package` | Read or overwrite the whole package |
| `POST` | `/api/automation_api/reload` | `{"domains": [...]}`, or an empty body for `homeassistant.reload_all` |
| `POST` | `/api/automation_api/restart` | Restart Home Assistant |

Helper domains: `input_boolean`, `input_datetime`, `input_number`,
`input_select`, `input_text`, `input_button`. Template types: `sensor`,
`binary_sensor`, `switch`, `button`, `number`, `select`.

### Lovelace dashboards

Only storage-mode dashboards can be written; YAML-mode dashboards are
read-only and writes return `400`. Use `default` as `{url_path}` for the
Overview dashboard. After every save, Home Assistant fires `lovelace_updated`.

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
| `GET` | `/api/automation_api/config_entries?domain=` | List integrations |
| `GET` `DELETE` | `/api/automation_api/config_entries/{entry_id}` | Read, or remove with all its devices and entities |
| `POST` | `/api/automation_api/config_entries/{entry_id}/reload` | Reload an integration |
| `POST` | `/api/automation_api/config_entries/{entry_id}/disable` | Disable an integration |
| `POST` | `/api/automation_api/config_entries/{entry_id}/enable` | Enable an integration |

Removing a registry entity while its integration is still active lets the
integration re-add it on the next reload; remove the device or the config
entry instead.

### Adding integrations (config flows)

Adds an integration the way *Add integration* in the UI does, step by step.
Every response is a flow step: `form` (answer with `{"user_input": {...}}`
matching `data_schema`), `menu` (`{"user_input": {"next_step_id": ...}}`),
`create_entry` (done, carries `entry_id`) or `abort` (`reason`). OAuth
integrations need a browser and can't be finished through the API.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/config_flows` | Flows in progress, including discovered integrations |
| `POST` | `/api/automation_api/config_flows` | Start a flow (`{domain}`) |
| `POST` | `/api/automation_api/config_flows/{flow_id}` | Submit `{user_input}`; without a body, return the current step |
| `DELETE` | `/api/automation_api/config_flows/{flow_id}` | Abort a flow |

### HACS

Search the HACS store and install or update repositories. HACS has no public
API, so this mirrors its own websocket commands (tested against HACS 2.0.5)
and may break when HACS changes.

Downloading installs third-party code that runs with full Home Assistant
rights, so it is **off by default**: enable *Allow HACS downloads* under
Settings → Devices & services → Automation API → Configure. Searching works
either way.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/automation_api/hacs/repositories?query=&category=&installed=&sort=&limit=` | Search the store; `sort` is `stars` (default), `last_updated` or `name`; 25 results by default |
| `POST` | `/api/automation_api/hacs/download` | Install or update (`{repository, category?, version?}`) |

`repository` is a HACS id or `owner/repo`. A repository that isn't in the
store is added as a custom repository first, which needs `category`
(`integration`, `plugin`, `theme`, ...). A downloaded integration needs a
restart (`restart_required: true`); then add it with a config flow.

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

## WebSocket API

Commands `automation_api/create`, `automation_api/delete` and
`automation_api/test`, with the same payloads as the REST endpoints.

## MCP server

The integration serves an MCP server over Streamable HTTP at
`/api/automation_api/mcp`. It offers one tool per REST endpoint, plus
`get_state` and `call_service`, and uses the same administrator bearer token.

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

## Notes

Writes go to `automations.yaml` and the package file, followed by a reload of
the affected domain. Every mutation is logged to `<config>/automation_api.log`.
All payloads are validated before anything is written.

## License

[MIT](LICENSE)
