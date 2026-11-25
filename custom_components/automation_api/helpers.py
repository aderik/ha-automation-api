"""`input_*` helpers, through the commands of the Helpers page.

Each `input_*` integration keeps its helpers in its own storage collection
and takes changes over its WebSocket commands (`input_boolean/create`,
`input_boolean/update`, ...), which is what Settings → Helpers uses. This
module sends those commands as the caller (see `ha_client`), so a helper
made here is an ordinary UI helper: validated by its integration, live at
once and editable in the UI.

Home Assistant derives the id of a new helper, and with it the entity id,
from its name. Helpers defined in YAML are not part of the collection and
are not listed.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .ha_client import connect as _connect

HELPER_DOMAINS: set[str] = {
    "input_boolean",
    "input_button",
    "input_datetime",
    "input_number",
    "input_select",
    "input_text",
}


def _check(domain: str, config: Any) -> None:
    if domain not in HELPER_DOMAINS:
        raise ValueError(f"unsupported helper domain: {domain}")
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    # Fields of a websocket message, never those of a helper
    if reserved := {"type", f"{domain}_id"} & config.keys():
        raise ValueError(f"{sorted(reserved)} are not fields of a helper")


def _with_entity_id(hass: HomeAssistant, domain: str, item: dict) -> dict:
    entity_id = er.async_get(hass).async_get_entity_id(domain, domain, item["id"])
    return {**item, "entity_id": entity_id}


async def list_helpers(hass: HomeAssistant, token: str | None, domain: str) -> dict:
    """The UI helpers of a domain, by id."""
    _check(domain, {})
    async with _connect(hass, token, ValueError) as send:
        items = await send(f"{domain}/list")
    return {item["id"]: _with_entity_id(hass, domain, item) for item in items}


async def get_helper(
    hass: HomeAssistant, token: str | None, domain: str, helper_id: str
) -> dict | None:
    return (await list_helpers(hass, token, domain)).get(helper_id)


async def create_helper(
    hass: HomeAssistant, token: str | None, domain: str, config: dict
) -> dict:
    """Create a helper from its full config; returns it with its id."""
    _check(domain, config)
    if "id" in config:
        raise ValueError("a new helper gets its id from its name; leave out 'id'")
    async with _connect(hass, token, ValueError) as send:
        item = await send(f"{domain}/create", **config)
    return _with_entity_id(hass, domain, item)


async def update_helper(
    hass: HomeAssistant, token: str | None, domain: str, helper_id: str, config: dict
) -> dict | None:
    """Replace the config of a helper; None if there is none with that id."""
    _check(domain, config)
    # A config read back from the list carries the id and entity id
    config = {k: v for k, v in config.items() if k not in ("id", "entity_id")}
    async with _connect(hass, token, ValueError) as send:
        if not any(item["id"] == helper_id for item in await send(f"{domain}/list")):
            return None
        item = await send(f"{domain}/update", **{f"{domain}_id": helper_id}, **config)
    return _with_entity_id(hass, domain, item)


async def delete_helper(
    hass: HomeAssistant, token: str | None, domain: str, helper_id: str
) -> bool:
    _check(domain, {})
    async with _connect(hass, token, ValueError) as send:
        if not any(item["id"] == helper_id for item in await send(f"{domain}/list")):
            return False
        await send(f"{domain}/delete", **{f"{domain}_id": helper_id})
    return True
