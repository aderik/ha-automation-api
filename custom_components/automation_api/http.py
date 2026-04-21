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
from . import package


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
    if not api_key:
        return False
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

        def _load():
            data = load_yaml(path) or []
            if isinstance(data, dict):
                return data.get("automation", [])
            return data

        items = await hass.async_add_executor_job(_load)

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


class _AuthedView(HomeAssistantView):
    """Base view enforcing the X-API-KEY header."""

    requires_auth = False

    def _check(self, request):
        return _check_api_key(request.app["hass"], request)

    def _unauth(self):
        return self.json({"error": "unauthorized"}, status_code=401)

    async def _json_body(self, request):
        try:
            data = await request.json()
        except Exception:
            return None, self.json({"error": "invalid json"}, status_code=400)
        return data, None


class PackageView(_AuthedView):
    url = "/api/automation_api/package"
    name = "api:automation_api:package"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        return self.json(await package.read_package(hass))

    async def put(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, "HTTP overwrite package")
        await package.overwrite_package(hass, data)
        reloaded = await package.reload_all(hass)
        return self.json({"status": "ok", "reloaded_all": reloaded})


class HelpersListView(_AuthedView):
    url = "/api/automation_api/helpers/{domain}"
    name = "api:automation_api:helpers:list"

    async def get(self, request, domain):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        if domain not in package.HELPER_DOMAINS:
            return self.json({"error": f"unsupported domain: {domain}"}, status_code=400)
        return self.json({"items": await package.list_helpers(hass, domain)})


class HelperItemView(_AuthedView):
    url = "/api/automation_api/helpers/{domain}/{helper_id}"
    name = "api:automation_api:helper"

    async def get(self, request, domain, helper_id):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        if domain not in package.HELPER_DOMAINS:
            return self.json({"error": f"unsupported domain: {domain}"}, status_code=400)
        item = await package.get_helper(hass, domain, helper_id)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def put(self, request, domain, helper_id):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        if domain not in package.HELPER_DOMAINS:
            return self.json({"error": f"unsupported domain: {domain}"}, status_code=400)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP upsert helper {domain}/{helper_id}")
        try:
            await package.upsert_helper(hass, domain, helper_id, data)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        reloaded = await package.reload_domain(hass, domain)
        return self.json(
            {"status": "ok", "domain": domain, "id": helper_id, "reloaded": reloaded}
        )

    async def delete(self, request, domain, helper_id):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        if domain not in package.HELPER_DOMAINS:
            return self.json({"error": f"unsupported domain: {domain}"}, status_code=400)
        await log(hass, f"HTTP delete helper {domain}/{helper_id}")
        removed = await package.delete_helper(hass, domain, helper_id)
        if not removed:
            return self.json({"error": "not found"}, status_code=404)
        reloaded = await package.reload_domain(hass, domain)
        return self.json(
            {"status": "ok", "domain": domain, "id": helper_id, "reloaded": reloaded}
        )


class TemplateListView(_AuthedView):
    url = "/api/automation_api/template/{ttype}"
    name = "api:automation_api:template:list"

    async def get(self, request, ttype):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        if ttype not in package.TEMPLATE_TYPES:
            return self.json({"error": f"unsupported template type: {ttype}"}, status_code=400)
        return self.json({"items": await package.list_templates(hass, ttype)})


class TemplateItemView(_AuthedView):
    url = "/api/automation_api/template/{ttype}/{name}"
    name = "api:automation_api:template:item"

    async def get(self, request, ttype, name):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        if ttype not in package.TEMPLATE_TYPES:
            return self.json({"error": f"unsupported template type: {ttype}"}, status_code=400)
        item = await package.get_template(hass, ttype, name)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def put(self, request, ttype, name):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        if ttype not in package.TEMPLATE_TYPES:
            return self.json({"error": f"unsupported template type: {ttype}"}, status_code=400)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP upsert template {ttype}/{name}")
        try:
            await package.upsert_template(hass, ttype, name, data)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        reloaded = await package.reload_domain(hass, "template")
        return self.json(
            {"status": "ok", "type": ttype, "name": name, "reloaded": reloaded}
        )

    async def delete(self, request, ttype, name):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        if ttype not in package.TEMPLATE_TYPES:
            return self.json({"error": f"unsupported template type: {ttype}"}, status_code=400)
        await log(hass, f"HTTP delete template {ttype}/{name}")
        removed = await package.delete_template(hass, ttype, name)
        if not removed:
            return self.json({"error": "not found"}, status_code=404)
        reloaded = await package.reload_domain(hass, "template")
        return self.json(
            {"status": "ok", "type": ttype, "name": name, "reloaded": reloaded}
        )


class HistoryStatsListView(_AuthedView):
    url = "/api/automation_api/history_stats"
    name = "api:automation_api:history_stats:list"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        return self.json({"items": await package.list_history_stats(hass)})


class HistoryStatsItemView(_AuthedView):
    url = "/api/automation_api/history_stats/{name}"
    name = "api:automation_api:history_stats:item"

    async def get(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        item = await package.get_history_stats(hass, name)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def put(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP upsert history_stats {name}")
        try:
            await package.upsert_history_stats(hass, name, data)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        return self.json(
            {"status": "ok", "name": name, "restart_required": True}
        )

    async def delete(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        await log(hass, f"HTTP delete history_stats {name}")
        removed = await package.delete_history_stats(hass, name)
        if not removed:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(
            {"status": "ok", "name": name, "restart_required": True}
        )


class NotifyGroupListView(_AuthedView):
    url = "/api/automation_api/notify_group"
    name = "api:automation_api:notify_group:list"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        return self.json({"items": await package.list_notify_groups(hass)})


class NotifyGroupItemView(_AuthedView):
    url = "/api/automation_api/notify_group/{name}"
    name = "api:automation_api:notify_group:item"

    async def get(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        item = await package.get_notify_group(hass, name)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def put(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        services = data.get("services")
        if services is None:
            return self.json({"error": "missing 'services'"}, status_code=400)
        extra = {k: v for k, v in data.items() if k != "services"}
        await log(hass, f"HTTP upsert notify_group {name}")
        try:
            await package.upsert_notify_group(hass, name, services, extra=extra)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        return self.json(
            {"status": "ok", "name": name, "restart_required": True}
        )

    async def delete(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        await log(hass, f"HTTP delete notify_group {name}")
        removed = await package.delete_notify_group(hass, name)
        if not removed:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(
            {"status": "ok", "name": name, "restart_required": True}
        )


class ReloadView(_AuthedView):
    url = "/api/automation_api/reload"
    name = "api:automation_api:reload"

    async def post(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        try:
            body = await request.json()
        except Exception:
            body = {}
        domains = body.get("domains") if isinstance(body, dict) else None
        if domains:
            if not isinstance(domains, list):
                return self.json({"error": "'domains' must be a list"}, status_code=400)
            results = {}
            for d in domains:
                results[d] = await package.reload_domain(hass, d)
            return self.json({"status": "ok", "results": results})
        ok = await package.reload_all(hass)
        return self.json({"status": "ok" if ok else "failed", "method": "reload_all"})


class RestartView(_AuthedView):
    url = "/api/automation_api/restart"
    name = "api:automation_api:restart"

    async def post(self, request):
        hass: HomeAssistant = request.app["hass"]
        if not self._check(request):
            return self._unauth()
        await log(hass, "HTTP restart requested")
        hass.async_create_task(package.restart(hass))
        return self.json({"status": "scheduled"})


def async_register_http(hass: HomeAssistant):
    hass.http.register_view(AutomationApiView)
    hass.http.register_view(AutomationApiTriggerView)
    hass.http.register_view(AutomationApiAreasView)
    hass.http.register_view(AutomationApiEntitiesView)
    hass.http.register_view(AutomationApiYamlView)
    hass.http.register_view(AutomationApiLogView)
    hass.http.register_view(PackageView)
    hass.http.register_view(HelpersListView)
    hass.http.register_view(HelperItemView)
    hass.http.register_view(TemplateListView)
    hass.http.register_view(TemplateItemView)
    hass.http.register_view(HistoryStatsListView)
    hass.http.register_view(HistoryStatsItemView)
    hass.http.register_view(NotifyGroupListView)
    hass.http.register_view(NotifyGroupItemView)
    hass.http.register_view(ReloadView)
    hass.http.register_view(RestartView)
