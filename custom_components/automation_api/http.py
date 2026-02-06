from __future__ import annotations

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_API_KEY
from .storage import create_or_update, delete as delete_automation, reload_automations


async def log(hass: HomeAssistant, message: str):
    await hass.services.async_call(
        "system_log",
        "write",
        {"message": f"[automation_api] {message}", "level": "info"},
        blocking=False,
    )


class AutomationApiView(HomeAssistantView):
    url = "/api/automation_api/automations"
    name = "api:automation_api:automations"
    requires_auth = False

    async def post(self, request):
        hass: HomeAssistant = request.app["hass"]
        entry = hass.data.get(DOMAIN, {}).get("entry")
        api_key = entry.data.get(CONF_API_KEY) if entry else None
        if request.headers.get("X-API-KEY") != api_key:
            return self.json({"error": "unauthorized"}, status_code=401)

        data = await request.json()
        await log(hass, f"HTTP create/update id={data.get('id')}")
        await create_or_update(hass, data)
        await reload_automations(hass)
        return self.json({"status": "ok", "id": data.get("id")})

    async def delete(self, request):
        hass: HomeAssistant = request.app["hass"]
        entry = hass.data.get(DOMAIN, {}).get("entry")
        api_key = entry.data.get(CONF_API_KEY) if entry else None
        if request.headers.get("X-API-KEY") != api_key:
            return self.json({"error": "unauthorized"}, status_code=401)

        automation_id = request.query.get("id")
        if not automation_id:
            return self.json({"error": "missing id"}, status_code=400)

        await log(hass, f"HTTP delete id={automation_id}")
        await delete_automation(hass, automation_id)
        await reload_automations(hass)
        return self.json({"status": "ok", "id": automation_id})


def async_register_http(hass: HomeAssistant):
    hass.http.register_view(AutomationApiView)
