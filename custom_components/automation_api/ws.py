from __future__ import annotations

from homeassistant.components import websocket_api


@websocket_api.websocket_command({"type": "automation_api/create"})
@websocket_api.async_response
async def ws_create(hass, connection, msg):
    connection.send_result(msg["id"], {"status": "ok"})


@websocket_api.websocket_command({"type": "automation_api/delete"})
@websocket_api.async_response
async def ws_delete(hass, connection, msg):
    connection.send_result(msg["id"], {"status": "ok"})


@websocket_api.websocket_command({"type": "automation_api/test"})
@websocket_api.async_response
async def ws_test(hass, connection, msg):
    connection.send_result(msg["id"], {"status": "ok"})


def async_register_ws(hass):
    websocket_api.async_register_command(hass, ws_create)
    websocket_api.async_register_command(hass, ws_delete)
    websocket_api.async_register_command(hass, ws_test)
