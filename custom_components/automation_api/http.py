from __future__ import annotations

import os

from aiohttp import web
from homeassistant import data_entry_flow
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import Unauthorized
import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import area_registry, entity_registry, device_registry
from homeassistant.util.yaml import load_yaml

from .const import DOMAIN, LOG_FILE
from .storage import create_or_update, delete as delete_automation, reload_automations
from .utils import log
from . import package
from . import lovelace as lovelace_mod
from . import registry as registry_mod
from . import history as history_mod
from . import hacs as hacs_mod


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


def _require_admin(request) -> None:
    """Every endpoint changes configuration, so mirror HA's own config API:
    the bearer token must belong to an administrator."""
    if not request["hass_user"].is_admin:
        raise Unauthorized()


class AutomationApiView(HomeAssistantView):
    url = "/api/automation_api/automations"
    name = "api:automation_api:automations"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)

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
        _require_admin(request)

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
        _require_admin(request)

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

    async def post(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)

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

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)

        reg = area_registry.async_get(hass)
        items = [
            {"id": a.id, "name": a.name}
            for a in reg.async_list_areas()
        ]
        return self.json({"items": items})


class AutomationApiEntitiesView(HomeAssistantView):
    url = "/api/automation_api/entities"
    name = "api:automation_api:entities"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)

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

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)

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

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)

        path = hass.config.path(LOG_FILE)
        if not path or not os.path.exists(path):
            return self.json({"error": "not found"}, status_code=404)
        return web.FileResponse(path, headers={"Content-Type": "text/plain; charset=utf-8"})


class _AuthedView(HomeAssistantView):
    """Base view with a JSON-body helper; auth is HA's own (bearer token, admin)."""

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
        _require_admin(request)
        return self.json(await package.read_package(hass))

    async def put(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
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
        _require_admin(request)
        if domain not in package.HELPER_DOMAINS:
            return self.json({"error": f"unsupported domain: {domain}"}, status_code=400)
        return self.json({"items": await package.list_helpers(hass, domain)})


class HelperItemView(_AuthedView):
    url = "/api/automation_api/helpers/{domain}/{helper_id}"
    name = "api:automation_api:helper"

    async def get(self, request, domain, helper_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        if domain not in package.HELPER_DOMAINS:
            return self.json({"error": f"unsupported domain: {domain}"}, status_code=400)
        item = await package.get_helper(hass, domain, helper_id)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def put(self, request, domain, helper_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
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
        _require_admin(request)
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
        _require_admin(request)
        if ttype not in package.TEMPLATE_TYPES:
            return self.json({"error": f"unsupported template type: {ttype}"}, status_code=400)
        return self.json({"items": await package.list_templates(hass, ttype)})


class TemplateItemView(_AuthedView):
    url = "/api/automation_api/template/{ttype}/{name}"
    name = "api:automation_api:template:item"

    async def get(self, request, ttype, name):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        if ttype not in package.TEMPLATE_TYPES:
            return self.json({"error": f"unsupported template type: {ttype}"}, status_code=400)
        item = await package.get_template(hass, ttype, name)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def put(self, request, ttype, name):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
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
        _require_admin(request)
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
        _require_admin(request)
        return self.json({"items": await package.list_history_stats(hass)})


class HistoryStatsItemView(_AuthedView):
    url = "/api/automation_api/history_stats/{name}"
    name = "api:automation_api:history_stats:item"

    async def get(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        item = await package.get_history_stats(hass, name)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def put(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
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
        _require_admin(request)
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
        _require_admin(request)
        return self.json({"items": await package.list_notify_groups(hass)})


class NotifyGroupItemView(_AuthedView):
    url = "/api/automation_api/notify_group/{name}"
    name = "api:automation_api:notify_group:item"

    async def get(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        item = await package.get_notify_group(hass, name)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def put(self, request, name):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
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
        _require_admin(request)
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
        _require_admin(request)
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
        _require_admin(request)
        await log(hass, "HTTP restart requested")
        hass.async_create_task(package.restart(hass))
        return self.json({"status": "scheduled"})


# --- Lovelace dashboards -------------------------------------------------

class LovelaceDashboardsView(_AuthedView):
    url = "/api/automation_api/lovelace/dashboards"
    name = "api:automation_api:lovelace:dashboards"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        try:
            items = await lovelace_mod.list_dashboards(hass)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        return self.json({"items": items})

    async def post(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        url_path = data.get("url_path")
        title = data.get("title")
        if not url_path or not title:
            return self.json(
                {"error": "'url_path' and 'title' are required"},
                status_code=400,
            )
        await log(hass, f"HTTP create dashboard {url_path}")
        try:
            created = await lovelace_mod.create_dashboard(
                hass,
                url_path=url_path,
                title=title,
                icon=data.get("icon"),
                show_in_sidebar=bool(data.get("show_in_sidebar", True)),
                require_admin=bool(data.get("require_admin", False)),
            )
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        except Exception as e:
            return self.json({"error": f"create failed: {e}"}, status_code=500)
        return self.json({"status": "ok", "dashboard": created})


class LovelaceDashboardItemView(_AuthedView):
    url = "/api/automation_api/lovelace/dashboards/{url_path}"
    name = "api:automation_api:lovelace:dashboard"

    async def patch(self, request, url_path):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP update dashboard {url_path}")
        try:
            updated = await lovelace_mod.update_dashboard(hass, url_path, data)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        except Exception as e:
            return self.json({"error": f"update failed: {e}"}, status_code=500)
        return self.json({"status": "ok", "dashboard": updated})

    async def delete(self, request, url_path):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        await log(hass, f"HTTP delete dashboard {url_path}")
        try:
            removed = await lovelace_mod.delete_dashboard(hass, url_path)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        if not removed:
            return self.json({"error": "not found"}, status_code=404)
        return self.json({"status": "ok", "url_path": url_path})


class LovelaceConfigView(_AuthedView):
    url = "/api/automation_api/lovelace/config/{url_path}"
    name = "api:automation_api:lovelace:config"

    async def get(self, request, url_path):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        try:
            cfg = await lovelace_mod.get_config(hass, url_path)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        if cfg is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(cfg)

    async def put(self, request, url_path):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP overwrite dashboard config {url_path}")
        try:
            await lovelace_mod.set_config(hass, url_path, data)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        except Exception as e:
            return self.json({"error": f"save failed: {e}"}, status_code=500)
        return self.json({"status": "ok", "url_path": url_path})


class LovelaceViewView(_AuthedView):
    url = "/api/automation_api/lovelace/view/{url_path}"
    name = "api:automation_api:lovelace:view:append"

    async def post(self, request, url_path):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP append view to {url_path}")
        try:
            result = await lovelace_mod.append_view(hass, url_path, data)
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)
        except Exception as e:
            return self.json({"error": f"append view failed: {e}"}, status_code=500)
        return self.json({"status": "ok", **result})


class LovelaceViewItemView(_AuthedView):
    url = "/api/automation_api/lovelace/view/{url_path}/{view_index}"
    name = "api:automation_api:lovelace:view:item"

    async def put(self, request, url_path, view_index):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        try:
            idx = int(view_index)
        except ValueError:
            return self.json({"error": "view_index must be int"}, status_code=400)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP replace view {url_path}/{idx}")
        try:
            result = await lovelace_mod.replace_view(hass, url_path, idx, data)
        except (ValueError, IndexError) as e:
            return self.json({"error": str(e)}, status_code=400)
        return self.json({"status": "ok", **result})

    async def delete(self, request, url_path, view_index):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        try:
            idx = int(view_index)
        except ValueError:
            return self.json({"error": "view_index must be int"}, status_code=400)
        await log(hass, f"HTTP delete view {url_path}/{idx}")
        try:
            result = await lovelace_mod.delete_view(hass, url_path, idx)
        except (ValueError, IndexError) as e:
            return self.json({"error": str(e)}, status_code=400)
        return self.json({"status": "ok", **result})


class LovelaceCardView(_AuthedView):
    url = "/api/automation_api/lovelace/card/{url_path}/{view_index}"
    name = "api:automation_api:lovelace:card:append"

    async def post(self, request, url_path, view_index):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        try:
            vidx = int(view_index)
        except ValueError:
            return self.json({"error": "view_index must be int"}, status_code=400)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP append card {url_path}/{vidx}")
        try:
            result = await lovelace_mod.append_card(hass, url_path, vidx, data)
        except (ValueError, IndexError) as e:
            return self.json({"error": str(e)}, status_code=400)
        return self.json({"status": "ok", **result})


class LovelaceCardItemView(_AuthedView):
    url = "/api/automation_api/lovelace/card/{url_path}/{view_index}/{card_index}"
    name = "api:automation_api:lovelace:card:item"

    async def put(self, request, url_path, view_index, card_index):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        try:
            vidx = int(view_index)
            cidx = int(card_index)
        except ValueError:
            return self.json({"error": "indices must be int"}, status_code=400)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP replace card {url_path}/{vidx}/{cidx}")
        try:
            result = await lovelace_mod.replace_card(
                hass, url_path, vidx, cidx, data
            )
        except (ValueError, IndexError) as e:
            return self.json({"error": str(e)}, status_code=400)
        return self.json({"status": "ok", **result})

    async def delete(self, request, url_path, view_index, card_index):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        try:
            vidx = int(view_index)
            cidx = int(card_index)
        except ValueError:
            return self.json({"error": "indices must be int"}, status_code=400)
        await log(hass, f"HTTP delete card {url_path}/{vidx}/{cidx}")
        try:
            result = await lovelace_mod.delete_card(hass, url_path, vidx, cidx)
        except (ValueError, IndexError) as e:
            return self.json({"error": str(e)}, status_code=400)
        return self.json({"status": "ok", **result})


# --- Entity / Device / Config-entries registries ------------------------

def _bool_query(request, name):
    v = request.query.get(name)
    if v is None:
        return None
    return v.lower() in ("1", "true", "yes")


class EntityRegistryListView(_AuthedView):
    url = "/api/automation_api/entity_registry"
    name = "api:automation_api:entity_registry:list"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        items = await registry_mod.list_entities(
            hass,
            domain=request.query.get("domain"),
            platform=request.query.get("platform"),
            device_id=request.query.get("device_id"),
            area_id=request.query.get("area_id"),
            config_entry_id=request.query.get("config_entry_id"),
            disabled=_bool_query(request, "disabled"),
        )
        return self.json({"items": items})


class EntityRegistryItemView(_AuthedView):
    url = "/api/automation_api/entity_registry/{entity_id}"
    name = "api:automation_api:entity_registry:item"

    async def get(self, request, entity_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        item = await registry_mod.get_entity(hass, entity_id)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def patch(self, request, entity_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP update entity_registry {entity_id}")
        try:
            updated = await registry_mod.update_entity(hass, entity_id, data)
        except Exception as e:
            return self.json({"error": f"update failed: {e}"}, status_code=500)
        if updated is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(updated)

    async def delete(self, request, entity_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        await log(hass, f"HTTP delete entity_registry {entity_id}")
        removed = await registry_mod.delete_entity(hass, entity_id)
        if not removed:
            return self.json({"error": "not found"}, status_code=404)
        return self.json({"status": "ok", "entity_id": entity_id})


class DeviceRegistryListView(_AuthedView):
    url = "/api/automation_api/device_registry"
    name = "api:automation_api:device_registry:list"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        items = await registry_mod.list_devices(
            hass,
            area_id=request.query.get("area_id"),
            manufacturer=request.query.get("manufacturer"),
            model=request.query.get("model"),
            integration=request.query.get("integration"),
            config_entry_id=request.query.get("config_entry_id"),
            disabled=_bool_query(request, "disabled"),
        )
        return self.json({"items": items})


class DeviceRegistryItemView(_AuthedView):
    url = "/api/automation_api/device_registry/{device_id}"
    name = "api:automation_api:device_registry:item"

    async def get(self, request, device_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        item = await registry_mod.get_device(hass, device_id)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def patch(self, request, device_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        data, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(data, dict):
            return self.json({"error": "body must be a dict"}, status_code=400)
        await log(hass, f"HTTP update device_registry {device_id}")
        try:
            updated = await registry_mod.update_device(hass, device_id, data)
        except Exception as e:
            return self.json({"error": f"update failed: {e}"}, status_code=500)
        if updated is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(updated)

    async def delete(self, request, device_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        await log(hass, f"HTTP delete device_registry {device_id}")
        removed = await registry_mod.delete_device(hass, device_id)
        if not removed:
            return self.json({"error": "not found"}, status_code=404)
        return self.json({"status": "ok", "device_id": device_id})


class ConfigEntryListView(_AuthedView):
    url = "/api/automation_api/config_entries"
    name = "api:automation_api:config_entries:list"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        items = await registry_mod.list_config_entries(
            hass, domain=request.query.get("domain")
        )
        return self.json({"items": items})


class ConfigEntryItemView(_AuthedView):
    url = "/api/automation_api/config_entries/{entry_id}"
    name = "api:automation_api:config_entries:item"

    async def get(self, request, entry_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        item = await registry_mod.get_config_entry(hass, entry_id)
        if item is None:
            return self.json({"error": "not found"}, status_code=404)
        return self.json(item)

    async def delete(self, request, entry_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        await log(hass, f"HTTP delete config_entry {entry_id}")
        try:
            ok = await registry_mod.remove_config_entry(hass, entry_id)
        except Exception as e:
            return self.json({"error": f"remove failed: {e}"}, status_code=500)
        if not ok:
            return self.json({"error": "not found or remove failed"}, status_code=404)
        return self.json({"status": "ok", "entry_id": entry_id})


class HistoryView(_AuthedView):
    url = "/api/automation_api/history"
    name = "api:automation_api:history"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)

        entity_id_param = request.query.get("entity_id")
        if not entity_id_param:
            return self.json({"error": "missing entity_id"}, status_code=400)
        entity_ids = [e.strip() for e in entity_id_param.split(",") if e.strip()]
        if not entity_ids:
            return self.json({"error": "missing entity_id"}, status_code=400)

        try:
            start_time, end_time = history_mod.parse_time_window(
                start=request.query.get("start"),
                end=request.query.get("end"),
                hours=request.query.get("hours"),
                days=request.query.get("days"),
            )
        except ValueError as e:
            return self.json({"error": str(e)}, status_code=400)

        significant = _bool_query(request, "significant") or False
        minimal = _bool_query(request, "minimal")
        if minimal is None:
            minimal = True
        no_attributes = _bool_query(request, "no_attributes")
        if no_attributes is None:
            no_attributes = True

        try:
            items = await history_mod.fetch_history(
                hass,
                entity_ids=entity_ids,
                start_time=start_time,
                end_time=end_time,
                significant=significant,
                minimal=minimal,
                no_attributes=no_attributes,
            )
        except Exception as e:
            return self.json({"error": f"history failed: {e}"}, status_code=500)

        return self.json(
            {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
                "counts": {eid: len(v) for eid, v in items.items()},
                "items": items,
            }
        )


class ConfigEntryActionView(_AuthedView):
    url = "/api/automation_api/config_entries/{entry_id}/{action}"
    name = "api:automation_api:config_entries:action"

    async def post(self, request, entry_id, action):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        await log(hass, f"HTTP config_entry action {entry_id} {action}")
        try:
            if action == "disable":
                ok = await registry_mod.disable_config_entry(
                    hass, entry_id, disable=True
                )
            elif action == "enable":
                ok = await registry_mod.disable_config_entry(
                    hass, entry_id, disable=False
                )
            elif action == "reload":
                ok = await registry_mod.reload_config_entry(hass, entry_id)
            else:
                return self.json(
                    {"error": f"unknown action: {action}"}, status_code=400
                )
        except Exception as e:
            return self.json({"error": f"{action} failed: {e}"}, status_code=500)
        if not ok:
            return self.json({"error": "not found"}, status_code=404)
        return self.json({"status": "ok", "entry_id": entry_id, "action": action})


async def _run_flow(view, coro):
    """Await a flow step and map flow errors to HTTP status codes."""
    try:
        return view.json(await coro)
    except (data_entry_flow.UnknownHandler, data_entry_flow.UnknownFlow) as e:
        return view.json(
            {"error": f"not found: {e or type(e).__name__}"}, status_code=404
        )
    except data_entry_flow.InvalidData as e:
        return view.json(
            {"error": "invalid user_input", "errors": e.schema_errors},
            status_code=400,
        )
    except data_entry_flow.UnknownStep as e:
        return view.json({"error": f"invalid user_input: {e}"}, status_code=400)
    except Exception as e:
        return view.json({"error": f"flow failed: {e}"}, status_code=500)


class ConfigFlowListView(_AuthedView):
    url = "/api/automation_api/config_flows"
    name = "api:automation_api:config_flows:list"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        return self.json({"items": await registry_mod.list_config_flows(hass)})

    async def post(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        body, err = await self._json_body(request)
        if err:
            return err
        domain = body.get("domain") if isinstance(body, dict) else None
        if not domain:
            return self.json({"error": "missing domain"}, status_code=400)
        await log(hass, f"HTTP start config_flow {domain}")
        return await _run_flow(self, registry_mod.start_config_flow(hass, domain))


class ConfigFlowItemView(_AuthedView):
    url = "/api/automation_api/config_flows/{flow_id}"
    name = "api:automation_api:config_flows:item"

    async def post(self, request, flow_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        try:
            body = await request.json()
        except Exception:  # no body: re-show the current step
            body = None
        user_input = body.get("user_input") if isinstance(body, dict) else None
        await log(hass, f"HTTP continue config_flow {flow_id}")
        return await _run_flow(
            self, registry_mod.continue_config_flow(hass, flow_id, user_input)
        )

    async def delete(self, request, flow_id):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        await log(hass, f"HTTP abort config_flow {flow_id}")
        try:
            await registry_mod.abort_config_flow(hass, flow_id)
        except data_entry_flow.UnknownFlow:
            return self.json({"error": "not found"}, status_code=404)
        return self.json({"status": "ok", "flow_id": flow_id})


class HacsRepositoriesView(_AuthedView):
    url = "/api/automation_api/hacs/repositories"
    name = "api:automation_api:hacs:repositories"

    async def get(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        try:
            limit = int(request.query.get("limit", 25))
        except ValueError:
            return self.json({"error": "limit must be an integer"}, status_code=400)
        try:
            items = await hacs_mod.search(
                hass,
                query=request.query.get("query"),
                category=request.query.get("category"),
                installed=_bool_query(request, "installed"),
                limit=limit,
            )
        except hacs_mod.HacsError as e:
            return self.json({"error": str(e)}, status_code=400)
        return self.json(
            {"downloads_allowed": hacs_mod.downloads_allowed(hass), "items": items}
        )


class HacsDownloadView(_AuthedView):
    url = "/api/automation_api/hacs/download"
    name = "api:automation_api:hacs:download"

    async def post(self, request):
        hass: HomeAssistant = request.app["hass"]
        _require_admin(request)
        body, err = await self._json_body(request)
        if err:
            return err
        if not isinstance(body, dict) or not body.get("repository"):
            return self.json({"error": "missing repository"}, status_code=400)
        await log(hass, f"HTTP hacs download {body['repository']}")
        try:
            result = await hacs_mod.download(
                hass,
                body["repository"],
                category=body.get("category"),
                version=body.get("version"),
            )
        except hacs_mod.HacsError as e:
            return self.json({"error": str(e)}, status_code=400)
        except Exception as e:
            return self.json({"error": f"download failed: {e}"}, status_code=500)
        return self.json(result)


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
    hass.http.register_view(LovelaceDashboardsView)
    hass.http.register_view(LovelaceDashboardItemView)
    hass.http.register_view(LovelaceConfigView)
    hass.http.register_view(LovelaceViewView)
    hass.http.register_view(LovelaceViewItemView)
    hass.http.register_view(LovelaceCardView)
    hass.http.register_view(LovelaceCardItemView)
    hass.http.register_view(EntityRegistryListView)
    hass.http.register_view(EntityRegistryItemView)
    hass.http.register_view(DeviceRegistryListView)
    hass.http.register_view(DeviceRegistryItemView)
    hass.http.register_view(ConfigEntryListView)
    hass.http.register_view(ConfigEntryItemView)
    hass.http.register_view(ConfigEntryActionView)
    hass.http.register_view(ConfigFlowListView)
    hass.http.register_view(ConfigFlowItemView)
    hass.http.register_view(HacsRepositoriesView)
    hass.http.register_view(HacsDownloadView)
    hass.http.register_view(HistoryView)
