"""Search, install and remove repositories through the Marketplace or HACS.

Home Assistant 2026.11 took HACS into core as the Marketplace integration;
older installs still have the HACS custom integration. This module is a
client of the store, like the store's own panel: it connects to this Home
Assistant's WebSocket API with the caller's access token and sends the
store's commands (`marketplace/repositories/list`,
`marketplace/repository/install`, ..., or their `hacs/...` counterparts of
HACS 2.0.5). The store checks and carries out every request itself, as that
user: administrator rights, the Marketplace's first-run warning and its
GitHub connection for custom repositories. The Marketplace wins when both
are set up.

Downloading runs third-party code with full Home Assistant rights, so on top
of the store's own checks it is off until the "Allow HACS downloads" option
of this integration is enabled.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from itertools import count
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ALLOW_HACS, DOMAIN


# sort name -> (key, descending)
_SORTS = {
    "stars": (lambda r: r.get("stars") or 0, True),
    "last_updated": (lambda r: r.get("last_updated") or "", True),
    "name": (lambda r: (r.get("name") or "").lower(), False),
}


# Removing these through the API would cut off HACS or this API itself.
_PROTECTED = {"hacs/integration", "aderik/ha-automation-api"}


# config entry domain of a store -> its command for each request made here;
# in order of preference
_COMMANDS = {
    "marketplace": {
        "list": "marketplace/repositories/list",
        "add": "marketplace/repositories/add",
        "install": "marketplace/repository/install",
        "uninstall": "marketplace/repository/uninstall",
    },
    "hacs": {
        "list": "hacs/repositories/list",
        "add": "hacs/repositories/add",
        "install": "hacs/repository/download",
        "uninstall": "hacs/repository/remove",
    },
}

# What a caller gets of a repository, out of what the store lists
_FIELDS = (
    "id",
    "full_name",
    "name",
    "category",
    "domain",
    "description",
    "installed",
    "installed_version",
    "available_version",
    "pending_upgrade",
    "stars",
    "downloads",
    "last_updated",
)

# Seconds for a whole request, the store's downloads from GitHub included
_TIMEOUT = 300

# Home Assistant's bind address -> where to reach it from the inside
_LOOPBACK = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "::": "::1"}


class HacsError(Exception):
    """A request the store can't fulfil; reported to the caller as a 400."""


def downloads_allowed(hass: HomeAssistant) -> bool:
    entry = hass.data.get(DOMAIN, {}).get("entry")
    return bool(entry and entry.options.get(CONF_ALLOW_HACS))


def _require_downloads_allowed(hass: HomeAssistant) -> None:
    if not downloads_allowed(hass):
        raise HacsError(
            "HACS downloads are disabled; enable 'Allow HACS downloads' in the "
            "Automation API integration options"
        )


async def _receive(ws) -> dict:
    msg = await ws.receive()
    if msg.type is not aiohttp.WSMsgType.TEXT:
        raise HacsError("Home Assistant closed the WebSocket connection")
    return msg.json()


@asynccontextmanager
async def _connect(hass: HomeAssistant, token: str):
    """Connect to this Home Assistant's WebSocket API as the caller.

    Yields `command(type, **fields)`, which returns the result of that
    command or raises `HacsError` with the error it was answered with.
    """
    api = hass.config.api
    host = _LOOPBACK.get(api.host, api.host)
    if ":" in host:
        host = f"[{host}]"
    url = f"{'wss' if api.use_ssl else 'ws'}://{host}:{api.port}/api/websocket"
    # The certificate names the hostname of Home Assistant, not this address
    session = async_get_clientsession(hass, verify_ssl=not api.use_ssl)
    try:
        # The list of a store is a few MB, more than the default message size
        async with (
            asyncio.timeout(_TIMEOUT),
            session.ws_connect(url, max_msg_size=0) as ws,
        ):
            await _receive(ws)  # auth_required
            await ws.send_json({"type": "auth", "access_token": token})
            if (await _receive(ws)).get("type") != "auth_ok":
                raise HacsError("Home Assistant refused the access token")
            ids = count(1)

            async def command(type_: str, **fields: Any) -> Any:
                msg_id = next(ids)
                await ws.send_json({"id": msg_id, "type": type_, **fields})
                while (reply := await _receive(ws)).get("id") != msg_id:
                    pass
                if not reply.get("success"):
                    error = reply.get("error") or {}
                    raise HacsError(f"{error.get('message')} ({error.get('code')})")
                return reply.get("result")

            yield command
    except TimeoutError as e:
        raise HacsError(
            f"no answer from the store within {_TIMEOUT} seconds; an install or "
            "removal may still finish, check with a search before retrying"
        ) from e
    except aiohttp.ClientError as e:
        raise HacsError(
            f"could not reach Home Assistant's WebSocket API at {url}: {e}"
        ) from e


@asynccontextmanager
async def _store(hass: HomeAssistant, token: str | None):
    """Yield `send(request, **fields)` for the store that is set up."""
    if not token:
        raise HacsError(
            "the store is asked as the caller; send the access token as "
            "`Authorization: Bearer <token>`"
        )
    commands = next(
        (
            commands
            for domain, commands in _COMMANDS.items()
            if hass.config_entries.async_entries(domain)
        ),
        None,
    )
    if commands is None:
        raise HacsError("neither the Marketplace nor HACS is set up")
    async with _connect(hass, token) as command:

        async def send(request: str, **fields: Any) -> Any:
            return await command(commands[request], **fields)

        yield send


def _normalize(repository: str) -> str:
    return repository.removeprefix("https://github.com/").strip("/")


def _find(rows: list[dict], repository: str) -> dict | None:
    """The row with that id or, in any case, that `owner/name`."""
    name = repository.lower()
    return next(
        (
            row
            for row in rows
            if str(row["id"]) == repository or row["full_name"].lower() == name
        ),
        None,
    )


def _repo_to_dict(row: dict) -> dict[str, Any]:
    return {field: row.get(field) for field in _FIELDS}


def _result(row: dict) -> dict[str, Any]:
    result = _repo_to_dict(row)
    result["restart_required"] = row.get("category") == "integration"
    return result


async def search(
    hass: HomeAssistant,
    token: str | None,
    *,
    query: str | None = None,
    category: str | None = None,
    installed: bool | None = None,
    sort: str | None = None,
    limit: int = 25,
) -> list[dict]:
    if (sort or "stars") not in _SORTS:
        raise HacsError(f"invalid sort {sort!r}, expected one of {sorted(_SORTS)}")
    async with _store(hass, token) as send:
        # ponytail: the stores have no search command, so every search fetches
        # the whole list (a few MB); cache it for a minute if that gets slow
        rows = await send("list", **({"categories": [category]} if category else {}))
    q = (query or "").lower()
    rows = [
        row
        for row in rows
        if (installed is None or row["installed"] == installed)
        and (
            not q
            or any(
                q in (row.get(field) or "").lower()
                for field in ("full_name", "name", "description", "domain")
            )
        )
    ]
    key, descending = _SORTS[sort or "stars"]
    rows.sort(key=key, reverse=descending)
    return [_repo_to_dict(row) for row in rows[:limit]]


async def download(
    hass: HomeAssistant,
    token: str | None,
    repository: str,
    *,
    category: str | None = None,
    version: str | None = None,
) -> dict:
    """Download (install or update) a repository by id or `owner/name`.

    A repository that isn't in the store is added as a custom repository
    first, which needs `category`.
    """
    _require_downloads_allowed(hass)
    repository = _normalize(repository)
    async with _store(hass, token) as send:
        rows = await send("list")
        row = _find(rows, repository)
        if row is None:
            row = await _add(send, rows, repository, category)
        await send(
            "install",
            repository=str(row["id"]),
            **({"version": version} if version else {}),
        )
        rows = await send("list", categories=[row["category"]])
        row = _find(rows, str(row["id"])) or row
    return _result(row)


async def _add(send, rows: list[dict], repository: str, category: str | None) -> dict:
    if not category:
        raise HacsError(
            f"{repository} is not in the store; pass `category` "
            f"(one of {sorted({row['category'] for row in rows})}) to add it "
            "as a custom repository"
        )
    await send("add", repository=repository, category=category)
    # HACS answers the same whether it added the repository or not, and
    # either store adds a renamed repository under its new name
    known = {row["id"] for row in rows}
    rows = await send("list")
    added = [row for row in rows if row["id"] not in known]
    row = _find(rows, repository) or (added[0] if len(added) == 1 else None)
    if row is None:
        raise HacsError(f"could not add {repository}; validation failed, see the log")
    return row


async def remove(hass: HomeAssistant, token: str | None, repository: str) -> dict:
    """Uninstall a downloaded repository by id or `owner/name`, as the
    store's own "Remove" does."""
    _require_downloads_allowed(hass)
    repository = _normalize(repository)
    async with _store(hass, token) as send:
        row = _find(await send("list"), repository)
        if row is None or not row["installed"]:
            raise HacsError(f"{repository} is not installed through the store")
        if row["full_name"].lower() in _PROTECTED:
            raise HacsError(
                f"refusing to remove {row['full_name']}; remove it in the UI"
            )
        await send("uninstall", repository=str(row["id"]))
        rows = await send("list", categories=[row["category"]])
        row = _find(rows, str(row["id"])) or {**row, "installed": False}
    return _result(row)
