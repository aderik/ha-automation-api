from __future__ import annotations

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, CONF_API_KEY
from .storage import create_or_update, delete as delete_automation, reload_automations
from .utils import log


CREATE_SCHEMA = vol.Schema(
    {
        vol.Required("id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("description", default=""): cv.string,
        vol.Required("trigger"): list,
        vol.Optional("condition", default=[]): list,
        vol.Required("action"): list,
        vol.Optional("mode", default="single"): cv.string,
    }
)


class AutomationApiView(HomeAssistantView):
    url = "/api/automation_api/automations"
    name = "api:automation_api:automations"
    requires_auth = False

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        entry = hass.data.get(DOMAIN, {}).get("entry")
        api_key = entry.data.get(CONF_API_KEY) if entry else None
        if request.headers.get("X-API-KEY") != api_key:
            return self.json({"error": "unauthorized"}, status_code=401)

        query_id = request.query.get("id")
        if query_id and not query_id.startswith("automation."):
            query_id = f"automation.{query_id}"

        states = hass.states.async_all("automation")
        items = []
        for st in states:
            if query_id and st.entity_id != query_id:
                continue
            attrs = dict(st.attributes)
            items.append(
                {
                    "entity_id": st.entity_id,
                    "id": st.entity_id.split(".", 1)[1],
                    "name": attrs.get("friendly_name"),
                    "state": st.state,
                    "last_triggered": attrs.get("last_triggered"),
                    "attributes": attrs,
                }
            )

        if query_id:
            if not items:
                return self.json({"error": "not found"}, status_code=404)
            return self.json(items[0])

        return self.json({"items": items})

    async def post(self, request):
        hass: HomeAssistant = request.app["hass"]
        entry = hass.data.get(DOMAIN, {}).get("entry")
        api_key = entry.data.get(CONF_API_KEY) if entry else None
        if request.headers.get("X-API-KEY") != api_key:
            return self.json({"error": "unauthorized"}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            return self.json({"error": "invalid json"}, status_code=400)
        try:
            data = CREATE_SCHEMA(data)
        except vol.Invalid as e:
            return self.json({"error": f"invalid payload: {e}"}, status_code=400)

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
