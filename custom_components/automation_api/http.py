from __future__ import annotations

import os

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import area_registry, entity_registry, device_registry
from homeassistant.util.yaml import load_yaml

from .const import DOMAIN, CONF_API_KEY, LOG_FILE
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


def _check_api_key(hass: HomeAssistant, request):
    entry = hass.data.get(DOMAIN, {}).get("entry")
    api_key = entry.data.get(CONF_API_KEY) if entry else None
    return request.headers.get("X-API-KEY") == api_key


class AutomationApiView(HomeAssistantView):
    url = "/api/automation_api/automations"
    name = "api:automation_api:automations"
    requires_auth = False

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not _check_api_key(hass, request):
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
        if not _check_api_key(hass, request):
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
        await reload_automations(hass, data.get("id"))
        return self.json({"status": "ok", "id": data.get("id")})

    async def delete(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not _check_api_key(hass, request):
            return self.json({"error": "unauthorized"}, status_code=401)

        automation_id = request.query.get("id")
        if not automation_id:
            return self.json({"error": "missing id"}, status_code=400)

        await log(hass, f"HTTP delete id={automation_id}")
        await delete_automation(hass, automation_id)
        await reload_automations(hass, automation_id)
        return self.json({"status": "ok", "id": automation_id})


class AutomationApiTriggerView(HomeAssistantView):
    url = "/api/automation_api/trigger"
    name = "api:automation_api:trigger"
    requires_auth = False

    async def post(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not _check_api_key(hass, request):
            return self.json({"error": "unauthorized"}, status_code=401)

        try:
            data = await request.json()
        except Exception:
            data = {}

        automation_id = data.get("id") or request.query.get("id")
        if not automation_id:
            return self.json({"error": "missing id"}, status_code=400)
        if not automation_id.startswith("automation."):
            automation_id = f"automation.{automation_id}"

        await log(hass, f"HTTP trigger entity_id={automation_id}")
        try:
            await hass.services.async_call(
                "automation", "trigger", {"entity_id": automation_id, "skip_condition": True}, blocking=True
            )
        except Exception:
            return self.json({"error": "automation not found or trigger failed"}, status_code=404)
        return self.json({"status": "ok", "entity_id": automation_id})


class AutomationApiAreasView(HomeAssistantView):
    url = "/api/automation_api/areas"
    name = "api:automation_api:areas"
    requires_auth = False

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not _check_api_key(hass, request):
            return self.json({"error": "unauthorized"}, status_code=401)

        reg = area_registry.async_get(hass)
        items = [
            {"id": a.id, "name": a.name}
            for a in reg.async_list_areas()
        ]
        return self.json({"items": items})


class AutomationApiEntitiesView(HomeAssistantView):
    url = "/api/automation_api/entities"
    name = "api:automation_api:entities"
    requires_auth = False

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not _check_api_key(hass, request):
            return self.json({"error": "unauthorized"}, status_code=401)

        domain = request.query.get("domain")
        area_name = request.query.get("area")
        search = (request.query.get("search") or "").lower()

        ar = area_registry.async_get(hass)
        er = entity_registry.async_get(hass)
        dr = device_registry.async_get(hass)

        area_id = None
        if area_name:
            for a in ar.async_list_areas():
                if a.name.lower() == area_name.lower():
                    area_id = a.id
                    break

        items = []
        for e in er.entities.values():
            if domain and not e.entity_id.startswith(domain + "."):
                continue

            effective_area_id = e.area_id
            if not effective_area_id and e.device_id:
                dev = dr.devices.get(e.device_id)
                if dev:
                    effective_area_id = dev.area_id

            if area_id and effective_area_id != area_id:
                continue
            if search and search not in (e.name or "").lower() and search not in e.entity_id.lower():
                continue
            items.append({
                "entity_id": e.entity_id,
                "name": e.name,
                "area_id": effective_area_id,
            })
        return self.json({"items": items})


class AutomationApiYamlView(HomeAssistantView):
    url = "/api/automation_api/automations_yaml"
    name = "api:automation_api:automations_yaml"
    requires_auth = False

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not _check_api_key(hass, request):
            return self.json({"error": "unauthorized"}, status_code=401)

        query_id = request.query.get("id")
        if query_id and query_id.startswith("automation."):
            query_id = query_id.split(".", 1)[1]

        path = hass.config.path("automations.yaml")
        data = load_yaml(path) or []
        if isinstance(data, dict):
            items = data.get("automation", [])
        else:
            items = data

        if query_id:
            for it in items:
                if it.get("id") == query_id:
                    return self.json(it)
            return self.json({"error": "not found"}, status_code=404)

        return self.json({"items": items})


class AutomationApiLogView(HomeAssistantView):
    url = "/api/automation_api/log"
    name = "api:automation_api:log"
    requires_auth = False

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not _check_api_key(hass, request):
            return self.json({"error": "unauthorized"}, status_code=401)

        path = hass.config.path(LOG_FILE)
        if not path or not os.path.exists(path):
            return self.json({"error": "not found"}, status_code=404)
        return web.FileResponse(path, headers={"Content-Type": "text/plain; charset=utf-8"})


def async_register_http(hass: HomeAssistant):
    hass.http.register_view(AutomationApiView)
    hass.http.register_view(AutomationApiTriggerView)
    hass.http.register_view(AutomationApiAreasView)
    hass.http.register_view(AutomationApiEntitiesView)
    hass.http.register_view(AutomationApiYamlView)
    hass.http.register_view(AutomationApiLogView)
