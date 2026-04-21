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
