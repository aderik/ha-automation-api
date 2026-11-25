"""A client of this Home Assistant's own WebSocket API, as the caller.

Some of what this integration offers belongs to another integration that
only takes requests over its WebSocket commands: the Marketplace, HACS and
Lovelace's list of dashboards. Their own panels use those commands; so does
this module, with the access token of the request. The other integration
then checks and carries out the request itself, as that user.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from itertools import count
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# Seconds for a whole request, a store's downloads from GitHub included
TIMEOUT = 300

# Home Assistant's bind address -> where to reach it from the inside
_LOOPBACK = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}


@asynccontextmanager
async def connect(hass: HomeAssistant, token: str | None, error: type[Exception]):
    """Connect to this Home Assistant's WebSocket API as the caller.

    Yields `command(type, **fields)`, which returns the result of that
    command. Whatever goes wrong, the error it was answered with included,
    is raised as `error`.
    """
    if not token:
        raise error(
            "this is done as the caller; send the access token as "
            "`Authorization: Bearer <token>`"
        )
    api = hass.config.api
    host = _LOOPBACK.get(api.host, api.host)
    if ":" in host:
        host = f"[{host}]"
    url = f"{'wss' if api.use_ssl else 'ws'}://{host}:{api.port}/api/websocket"
    # The certificate names the hostname of Home Assistant, not this address
    session = async_get_clientsession(hass, verify_ssl=not api.use_ssl)

    async def receive(ws) -> dict:
        msg = await ws.receive()
        if msg.type is not aiohttp.WSMsgType.TEXT:
            raise error("Home Assistant closed the WebSocket connection")
        return msg.json()

    try:
        # The list of a store is a few MB, more than the default message size
        async with (
            asyncio.timeout(TIMEOUT),
            session.ws_connect(url, max_msg_size=0) as ws,
        ):
            await receive(ws)  # auth_required
            await ws.send_json({"type": "auth", "access_token": token})
            if (await receive(ws)).get("type") != "auth_ok":
                raise error("Home Assistant refused the access token")
            ids = count(1)

            async def command(type_: str, **fields: Any) -> Any:
                msg_id = next(ids)
                await ws.send_json({**fields, "id": msg_id, "type": type_})
                while (reply := await receive(ws)).get("id") != msg_id:
                    pass
                if not reply.get("success"):
                    failure = reply.get("error") or {}
                    raise error(f"{failure.get('message')} ({failure.get('code')})")
                return reply.get("result")

            yield command
    except TimeoutError as e:
        raise error(
            f"no answer from Home Assistant within {TIMEOUT} seconds; the "
            "request may still finish, check before retrying"
        ) from e
    except aiohttp.ClientError as e:
        raise error(
            f"could not reach Home Assistant's WebSocket API at {url}: {e}"
        ) from e
