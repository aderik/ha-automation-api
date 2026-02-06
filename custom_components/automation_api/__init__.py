from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .http import async_register_http
from .ws import async_register_ws
from .storage import create_or_update, delete as delete_automation, reload_automations


async def async_setup(hass: HomeAssistant, config: dict):
    hass.data.setdefault(DOMAIN, {})
    async_register_http(hass)
    async_register_ws(hass)

    async def handle_create(call):
        data = {
            "id": call.data.get("automation_id"),
            "name": call.data.get("name"),
            "description": call.data.get("description", ""),
            "trigger": call.data.get("trigger", []),
            "condition": call.data.get("condition", []),
            "action": call.data.get("action", []),
            "mode": call.data.get("mode", "single"),
        }
        await create_or_update(hass, data)
        await reload_automations(hass)

    async def handle_delete(call):
        await delete_automation(hass, call.data.get("automation_id"))
        await reload_automations(hass)

    async def handle_test(call):
        await hass.services.async_call(
            "automation", "trigger", {"entity_id": call.data.get("entity_id"), "skip_condition": True}, blocking=True
        )

    hass.services.async_register(DOMAIN, "create_automation", handle_create)
    hass.services.async_register(DOMAIN, "delete_automation", handle_delete)
    hass.services.async_register(DOMAIN, "test_automation", handle_test)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data[DOMAIN]["entry"] = entry
    return True
