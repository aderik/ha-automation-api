from __future__ import annotations

from homeassistant.components import websocket_api

from .storage import create_or_update, delete as delete_automation, reload_automations


@websocket_api.websocket_command({"type": "automation_api/create"})
@websocket_api.async_response
async def ws_create(hass, connection, msg):
    await create_or_update(hass, msg)
    await reload_automations(hass)
    connection.send_result(msg["id"], {"status": "ok", "id": msg.get("id")})


@websocket_api.websocket_command({"type": "automation_api/delete"})
@websocket_api.async_response
async def ws_delete(hass, connection, msg):
    await delete_automation(hass, msg.get("automation_id"))
    await reload_automations(hass)
    connection.send_result(msg["id"], {"status": "ok", "id": msg.get("automation_id")})


@websocket_api.websocket_command({"type": "automation_api/test"})
@websocket_api.async_response
async def ws_test(hass, connection, msg):
    entity_id = msg.get("entity_id")
    await hass.services.async_call(
        "automation", "trigger", {"entity_id": entity_id, "skip_condition": True}, blocking=True
    )
    connection.send_result(msg["id"], {"status": "ok", "entity_id": entity_id})


def async_register_ws(hass):
    websocket_api.async_register_command(hass, ws_create)
    websocket_api.async_register_command(hass, ws_delete)
    websocket_api.async_register_command(hass, ws_test)
