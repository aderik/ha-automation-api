from __future__ import annotations

from homeassistant.components import websocket_api
import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .storage import create_or_update, delete as delete_automation, reload_automations
from .utils import log, normalize_payload


@websocket_api.websocket_command(
    {
        vol.Required("type"): "automation_api/create",
        vol.Required("automation_id"): cv.string,
        vol.Required("name"): cv.string,
        vol.Required("trigger"): list,
        vol.Optional("condition", default=[]): list,
        vol.Required("action"): list,
        vol.Optional("description", default=""): cv.string,
        vol.Optional("mode", default="single"): cv.string,
    }
)
@websocket_api.async_response
async def ws_create(hass, connection, msg):
    data = normalize_payload(msg)
    await log(hass, f"WS create/update id={data.get('id')}")
    await create_or_update(hass, data)
    await reload_automations(hass)
    connection.send_result(msg["id"], {"status": "ok", "id": data.get("id")})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "automation_api/delete",
        vol.Required("automation_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_delete(hass, connection, msg):
    await log(hass, f"WS delete id={msg.get('automation_id')}")
    await delete_automation(hass, msg.get("automation_id"))
    await reload_automations(hass)
    connection.send_result(msg["id"], {"status": "ok", "id": msg.get("automation_id")})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "automation_api/test",
        vol.Required("entity_id"): cv.string,
    }
)
@websocket_api.async_response
async def ws_test(hass, connection, msg):
    entity_id = msg.get("entity_id")
    await log(hass, f"WS trigger entity_id={entity_id}")
    await hass.services.async_call(
        "automation", "trigger", {"entity_id": entity_id, "skip_condition": True}, blocking=True
    )
    connection.send_result(msg["id"], {"status": "ok", "entity_id": entity_id})


def async_register_ws(hass):
    websocket_api.async_register_command(hass, ws_create)
    websocket_api.async_register_command(hass, ws_delete)
    websocket_api.async_register_command(hass, ws_test)
