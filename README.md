<p align="center">
  <a href="https://github.com/aderik/ha-automation-api/releases">
    <img src="https://img.shields.io/github/v/release/aderik/ha-automation-api?style=for-the-badge" />
  </a>
  <a href="https://github.com/hacs/integration">
    <img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/github/license/aderik/ha-automation-api?style=for-the-badge" />
  </a>
  <a href="https://www.buymeacoffee.com/aderik">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" />
  </a>
</p>

# Automation API (Home Assistant custom integration)

**Full automation CRUD + trigger + list for Home Assistant** — via simple REST + WebSocket endpoints with an API‑key.

If you want to manage automations from **AI agents**, **n8n/Make**, **custom dashboards**, or **CI/CD pipelines**, this integration fills a gap in HA’s built‑in APIs.

---

## ✨ Why this integration?
- ✅ **Full REST CRUD + trigger + list** (not just create/update)
- ✅ **API‑key auth** (simpler for machine‑to‑machine than LLATs)
- ✅ **HACS‑installable**
- ✅ **Writes to `automations.yaml`** — same flow as the HA UI
- ✅ **WebSocket API** for real‑time integrations

## 🏆 How it compares
| Feature | HA built‑in config API | Automation API (this) |
|---|---|---|
| Documented & intended for external use | ❌ | ✅ |
| REST **list** automations | ❌ | ✅ |
| REST **trigger** automations | ❌ | ✅ |
| Simple API‑key auth | ❌ (LLAT only) | ✅ |
| HACS install | ❌ | ✅ |

---

## 🚀 Install (HACS)
1. Add this repo as a **custom repository** (type: Integration).
2. Install and restart Home Assistant.
3. Add integration: **Settings → Devices & Services → Add Integration → Automation API**.
4. Copy the generated API key.

---

## 📡 REST API
All REST endpoints require `X-API-KEY`.

- **List automations**: `GET /api/automation_api/automations`
- **Get automation**: `GET /api/automation_api/automations?id=solaredge_power_notify`
- **Create/Update**: `POST /api/automation_api/automations`
- **Delete**: `DELETE /api/automation_api/automations?id=solaredge_power_notify`
- **Trigger**: `POST /api/automation_api/trigger` (body: `{"id":"solaredge_power_notify"}`)
- **Get automation YAML**: `GET /api/automation_api/automations_yaml?id=solaredge_power_notify`
- **List areas**: `GET /api/automation_api/areas`
- **List entities**: `GET /api/automation_api/entities?domain=light&area=Woonkamer&search=venster`

<details>
<summary><strong>Klik om cURL‑voorbeelden te zien</strong></summary>

```bash
curl -H "X-API-KEY: YOUR_KEY" http://ha:8123/api/automation_api/automations
```

```bash
curl -H "X-API-KEY: YOUR_KEY" \
  "http://ha:8123/api/automation_api/automations_yaml?id=example"
```

```bash
curl -X POST -H "X-API-KEY: YOUR_KEY" -H "Content-Type: application/json" \
  http://ha:8123/api/automation_api/automations \
  -d '{"id":"example","name":"Example","trigger":[],"action":[]}'
```

```bash
curl -X POST -H "X-API-KEY: YOUR_KEY" -H "Content-Type: application/json" \
  http://ha:8123/api/automation_api/trigger \
  -d '{"id":"example"}'
```

```bash
curl -H "X-API-KEY: YOUR_KEY" \
  "http://ha:8123/api/automation_api/areas"
```

```bash
curl -H "X-API-KEY: YOUR_KEY" \
  "http://ha:8123/api/automation_api/entities?domain=light&area=Woonkamer"
```

</details>

---

## 🧩 Managed configuration (packages)
Beyond automations, the integration can manage **helpers, template sensors, history_stats sensors, and notify groups** via a single HA package file at `<config>/packages/automation_api.yaml`.

**One‑time setup** — add this to your `configuration.yaml`:
```yaml
homeassistant:
  packages: !include_dir_named packages
```
Then restart HA once. After that, every endpoint below reloads the relevant domain automatically.

### Helper endpoints (dict‑keyed: one id per helper)
Supported domains: `input_boolean`, `input_datetime`, `input_number`, `input_select`, `input_text`, `input_button`.

- **Upsert**: `PUT /api/automation_api/helpers/{domain}/{id}` — body: full config dict
- **Get / Delete**: `GET|DELETE /api/automation_api/helpers/{domain}/{id}`
- **List**: `GET /api/automation_api/helpers/{domain}`

### Template entities
Supported types: `sensor`, `binary_sensor`, `switch`, `button`, `number`, `select`.

- **Upsert**: `PUT /api/automation_api/template/{type}/{name}` — body: template config (without `name`)
- **Get / Delete**: `GET|DELETE /api/automation_api/template/{type}/{name}`
- **List**: `GET /api/automation_api/template/{type}`

### History stats sensors
- **Upsert**: `PUT /api/automation_api/history_stats/{name}` — body: `{entity_id, state, type, duration, ...}`
- **Get / Delete / List**: as above. Requires a HA restart to take effect.

### Notify groups
- **Upsert**: `PUT /api/automation_api/notify_group/{name}` — body: `{"services": ["mobile_app_x", ...]}`
- Requires a HA restart to take effect.

### Reload / restart
- `POST /api/automation_api/reload` — body `{"domains": ["input_boolean", "template"]}` (or empty for `homeassistant.reload_all`)
- `POST /api/automation_api/restart` — triggers `homeassistant.restart`

### Raw package
- `GET /api/automation_api/package` — returns the full managed package as JSON
- `PUT /api/automation_api/package` — overwrites it (body: dict)

> ⚠️ `packages/automation_api.yaml` is owned by this integration. Don't hand‑edit it; use the API.

---

## 🎨 Lovelace dashboards
Create and edit **storage‑mode** Lovelace dashboards programmatically. YAML‑mode dashboards are read‑only and writes are refused with `400`.

### Dashboards themselves
- `GET /api/automation_api/lovelace/dashboards` — list all
- `POST /api/automation_api/lovelace/dashboards` — create new (`{url_path, title, icon?, show_in_sidebar?, require_admin?}`)
- `PATCH /api/automation_api/lovelace/dashboards/{url_path}` — update metadata
- `DELETE /api/automation_api/lovelace/dashboards/{url_path}`

Use `default` as `{url_path}` to reference the Overview dashboard.

### Full config
- `GET /api/automation_api/lovelace/config/{url_path}` — returns `{views: [...], ...}`
- `PUT /api/automation_api/lovelace/config/{url_path}` — overwrite

### Views (append / replace / delete inside a dashboard)
- `POST /api/automation_api/lovelace/view/{url_path}` — append view, body: view dict
- `PUT /api/automation_api/lovelace/view/{url_path}/{view_index}` — replace
- `DELETE /api/automation_api/lovelace/view/{url_path}/{view_index}`

### Cards (append / replace / delete inside a view)
- `POST /api/automation_api/lovelace/card/{url_path}/{view_index}` — append card
- `PUT /api/automation_api/lovelace/card/{url_path}/{view_index}/{card_index}` — replace
- `DELETE /api/automation_api/lovelace/card/{url_path}/{view_index}/{card_index}`

After any save, HA fires `lovelace_updated` so open browsers pick up the change on next refresh.

---

## 📜 History (state changes)
Query Home Assistant's recorder for state-change history of any entity. Useful for diagnosing flapping sensors or inspecting when an automation last ran.

- `GET /api/automation_api/history?entity_id=sensor.x&hours=24` — last 24h of state changes
- Multiple entities: `entity_id=sensor.x,sensor.y`
- Time window:
  - `hours=24` or `days=7` — relative to `end` (default now)
  - `start=2026-05-22T00:00:00+00:00&end=2026-05-23T00:00:00+00:00` — absolute
- Modifiers:
  - `significant=true` — use HA's `get_significant_states` (filtered)
  - `minimal=false` — include `last_updated` as well as `last_changed`
  - `no_attributes=false` — keep attributes in the payload

Response includes a `counts` map per entity (great for spotting flapping at a glance):
```json
{
  "start": "2026-05-22T11:00:00+00:00",
  "end":   "2026-05-23T11:00:00+00:00",
  "counts": {"sensor.weer_verwachting_komende_uren": 47},
  "items":  {"sensor.weer_verwachting_komende_uren": [{"state": "...", "last_changed": "..."}]}
}
```

---

## 🧹 Registry management
Read and mutate Home Assistant's internal registries: useful for cleaning up orphan/duplicate entities, renaming devices, or disabling integrations entirely.

### Entity registry
- `GET /api/automation_api/entity_registry?domain=&platform=&device_id=&area_id=&config_entry_id=&disabled=` — list with filters
- `GET /api/automation_api/entity_registry/{entity_id}` — full details (incl. `unique_id`, `platform`, `device_id`, `disabled_by`, `hidden_by`, `original_name`)
- `PATCH /api/automation_api/entity_registry/{entity_id}` — update `name`, `icon`, `area_id`, `new_entity_id`, `disabled_by` (bool), `hidden_by` (bool)
- `DELETE /api/automation_api/entity_registry/{entity_id}` — remove from registry (integration may re-add on reload)

### Device registry
- `GET /api/automation_api/device_registry?area_id=&manufacturer=&model=&integration=&config_entry_id=&disabled=` — list with filters
- `GET /api/automation_api/device_registry/{device_id}` — full details
- `PATCH /api/automation_api/device_registry/{device_id}` — update `name_by_user`, `area_id`, `disabled_by`
- `DELETE /api/automation_api/device_registry/{device_id}` — remove device + cascades to its entities

### Config entries (integrations)
- `GET /api/automation_api/config_entries?domain=` — list installed integrations
- `GET /api/automation_api/config_entries/{entry_id}`
- `POST /api/automation_api/config_entries/{entry_id}/disable` — soft-disable
- `POST /api/automation_api/config_entries/{entry_id}/enable`
- `POST /api/automation_api/config_entries/{entry_id}/reload`
- `DELETE /api/automation_api/config_entries/{entry_id}` — fully remove integration (cascades to all its devices/entities)

---

## 🔌 WebSocket
- `automation_api/create`
- `automation_api/delete`
- `automation_api/test`

---

## 📝 Notes
Implementation writes to `automations.yaml` (like the HA UI), reloads automations, and logs actions to HA system log. REST/WS payloads are validated.

---

<p align="center">
  <a href="https://www.buymeacoffee.com/aderik">
    <img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-FFDD00?style=for-the-badge&logo=buymeacoffee&logoColor=black" />
  </a>
</p>
