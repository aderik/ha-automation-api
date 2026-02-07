# Automation API (Home Assistant custom integration)

**Full automation CRUD + trigger + list for Home Assistant** — via simple REST + WebSocket endpoints with an API‑key.

If you want to manage automations from **AI agents**, **n8n/Make**, **custom dashboards**, or **CI/CD pipelines**, this integration fills a gap in HA’s built‑in APIs.

## Why this integration?
- ✅ **Full REST CRUD + trigger + list** (not just create/update)
- ✅ **API‑key auth** (simpler for machine‑to‑machine than LLATs)
- ✅ **HACS‑installable**
- ✅ **Writes to `automations.yaml`** — same flow as the HA UI
- ✅ **WebSocket API** for real‑time integrations

## How it compares
| Feature | HA built‑in config API | Automation API (this)
|---|---|---|
| Documented & intended for external use | ❌ | ✅
| REST **list** automations | ❌ | ✅
| REST **trigger** automations | ❌ | ✅
| Simple API‑key auth | ❌ (LLAT only) | ✅
| HACS install | ❌ | ✅

## Install (HACS)
1. Add this repo as a **custom repository** (type: Integration).
2. Install and restart Home Assistant.
3. Add integration: **Settings → Devices & Services → Add Integration → Automation API**.
4. Copy the generated API key.

## REST
All REST endpoints require `X-API-KEY`.

- **List**: `GET /api/automation_api/automations`
- **Get one**: `GET /api/automation_api/automations?id=solaredge_power_notify`
- **Create/Update**: `POST /api/automation_api/automations`
- **Delete**: `DELETE /api/automation_api/automations?id=solaredge_power_notify`
- **Trigger**: `POST /api/automation_api/trigger` (body: `{"id":"solaredge_power_notify"}`)

## WebSocket
- `automation_api/create`
- `automation_api/delete`
- `automation_api/test`

## Notes
Implementation writes to `automations.yaml` (like the HA UI), reloads automations, and logs actions to HA system log. REST/WS payloads are validated.
