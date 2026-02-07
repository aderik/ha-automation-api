from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .http import async_register_http
from .ws import async_register_ws
from .storage import create_or_update, delete as delete_automation, reload_automations
from .utils import log, normalize_payload


async def async_setup(hass: HomeAssistant, config: dict):
    hass.data.setdefault(DOMAIN, {})
    async_register_http(hass)
    async_register_ws(hass)

    create_schema = vol.Schema(
        {
            vol.Required("automation_id"): cv.string,
            vol.Required("name"): cv.string,
            vol.Required("trigger"): list,
            vol.Optional("condition", default=[]): list,
            vol.Required("action"): list,
            vol.Optional("description", default=""): cv.string,
            vol.Optional("mode", default="single"): cv.string,
        }
    )
    delete_schema = vol.Schema({vol.Required("automation_id"): cv.string})
    test_schema = vol.Schema({vol.Required("entity_id"): cv.string})

    async def handle_create(call):
        data = normalize_payload(call.data)
        await log(hass, f"service create_automation id={data.get('id')}")
        await create_or_update(hass, data)
        await reload_automations(hass, data.get("id"))

    async def handle_delete(call):
        await log(hass, f"service delete_automation id={call.data.get('automation_id')}")
        await delete_automation(hass, call.data.get("automation_id"))
        await reload_automations(hass, call.data.get("automation_id"))

    async def handle_test(call):
        await log(hass, f"service test_automation entity_id={call.data.get('entity_id')}")
        await hass.services.async_call(
            "automation", "trigger", {"entity_id": call.data.get("entity_id"), "skip_condition": True}, blocking=True
        )

    hass.services.async_register(DOMAIN, "create_automation", handle_create, schema=create_schema)
    hass.services.async_register(DOMAIN, "delete_automation", handle_delete, schema=delete_schema)
    hass.services.async_register(DOMAIN, "test_automation", handle_test, schema=test_schema)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})["entry"] = entry
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    if DOMAIN in hass.data:
        hass.data[DOMAIN].pop("entry", None)
    return True
