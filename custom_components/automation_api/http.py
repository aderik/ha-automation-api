from __future__ import annotations

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_API_KEY


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
        # TODO: create/update automation via HA internal APIs
        return self.json({"status": "ok", "id": data.get("id")})

    async def delete(self, request):
        hass: HomeAssistant = request.app["hass"]
        entry = hass.data.get(DOMAIN, {}).get("entry")
        api_key = entry.data.get(CONF_API_KEY) if entry else None
        if request.headers.get("X-API-KEY") != api_key:
            return self.json({"error": "unauthorized"}, status_code=401)
        # TODO: delete automation via HA internal APIs
        return self.json({"status": "ok"})


def async_register_http(hass: HomeAssistant):
    hass.http.register_view(AutomationApiView)
