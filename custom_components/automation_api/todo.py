"""To-do lists: read and change their items.

A to-do list is an entity of some integration (the local to-do list, the
shopping list, Google Tasks, ...). Its items are read with the `todo/item/list`
WebSocket command, sent as the caller (see `ha_client`), and changed with
the `todo.add_item`, `todo.update_item` and `todo.remove_item` services, so
the integration behind the list does the work and refuses what it can't do.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .ha_client import connect as _connect

STATUSES = ("needs_action", "completed")

# The fields of an item the services take, next to `item` itself
_FIELDS = ("due_date", "due_datetime", "description")


async def list_items(
    hass: HomeAssistant, token: str | None, entity_id: str, status: str | None = None
) -> list[dict] | None:
    """The items of a list, in its own order; None if there is no such list."""
    if status not in (None, *STATUSES):
        raise ValueError(f"status must be one of {list(STATUSES)}")
    async with _connect(hass, token, ValueError) as send:
        try:
            found = await send("todo/item/list", entity_id=entity_id)
        except ValueError as e:
            if str(e).endswith("(not_found)"):
                return None
            raise
    return [item for item in found["items"] if status in (None, item.get("status"))]


async def _call(
    hass: HomeAssistant, user_id: str | None, service: str, entity_id: str, data: dict
) -> None:
    if hass.states.get(entity_id) is None or not entity_id.startswith("todo."):
        raise LookupError(entity_id)
    try:
        await hass.services.async_call(
            "todo",
            service,
            {"entity_id": entity_id, **data},
            blocking=True,
            context=Context(user_id=user_id),
        )
    except (HomeAssistantError, ValueError) as e:  # what the list refuses
        raise ValueError(str(e)) from e


def _given(config: Any, allowed: tuple[str, ...]) -> dict:
    if not isinstance(config, dict):
        raise ValueError("body must be a dict")
    if unknown := set(config) - set(allowed):
        raise ValueError(f"an item takes {list(allowed)}, not {sorted(unknown)}")
    return {k: v for k, v in config.items() if v is not None}


async def add_item(
    hass: HomeAssistant, user_id: str | None, entity_id: str, config: Any
) -> None:
    """Add an item: `item` is its text; a due date or description where the
    list supports those."""
    data = _given(config, ("item", *_FIELDS))
    if not isinstance(data.get("item"), str) or not data["item"].strip():
        raise ValueError("an item needs its text in 'item'")
    await _call(hass, user_id, "add_item", entity_id, data)


async def update_item(
    hass: HomeAssistant, user_id: str | None, entity_id: str, item: str, config: Any
) -> None:
    """Change an item, named by its uid or its text: `rename`, `status`, a
    due date or the description."""
    data = _given(config, ("rename", "status", *_FIELDS))
    if not data:
        raise ValueError("give something to change")
    if data.get("status") not in (None, *STATUSES):
        raise ValueError(f"status must be one of {list(STATUSES)}")
    await _call(hass, user_id, "update_item", entity_id, {"item": item, **data})


async def remove_item(
    hass: HomeAssistant, user_id: str | None, entity_id: str, item: str
) -> None:
    """Remove an item, named by its uid or its text."""
    await _call(hass, user_id, "remove_item", entity_id, {"item": [item]})
