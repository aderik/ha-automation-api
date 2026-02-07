# Automation API (Home Assistant custom integration)

Minimal scaffold to add REST/WS endpoints for automation create/update/delete/test.

## Install (HACS)
1. Add this repo as a **custom repository** (type: Integration).
2. Install and restart Home Assistant.
3. Add integration: **Settings → Devices & Services → Add Integration → Automation API**.
4. Copy the generated API key.

## REST
- POST /api/automation_api/automations (X-API-KEY)
- DELETE /api/automation_api/automations?id=...

## WS
- automation_api/create
- automation_api/delete
- automation_api/test

Implementation: writes to automations.yaml (like HA UI), reloads automations, logs actions to HA system log. REST/WS payloads are validated. GET /api/automation_api/automations lists current HA automations. POST /api/automation_api/trigger triggers an automation by id.

All REST endpoints require X-API-KEY.
