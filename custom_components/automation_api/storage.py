from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from .const import DOMAIN

STORE_VERSION = 1
STORE_KEY = "automation_api"


def _store(hass: HomeAssistant) -> Store:
    store = hass.data.setdefault(DOMAIN, {}).get("store")
    if store is None:
        store = Store(hass, STORE_VERSION, STORE_KEY)
        hass.data.setdefault(DOMAIN, {})["store"] = store
    return store


def _normalize_item(item: dict) -> dict:
    item = dict(item)
    item.setdefault("condition", [])
    item.setdefault("description", "")
    item.setdefault("mode", "single")
    return item


def _upsert(items: list[dict], item: dict) -> list[dict]:
    item = _normalize_item(item)
    new = []
    found = False
    for it in items:
        if it.get("id") == item.get("id"):
            new.append(item)
            found = True
        else:
            new.append(it)
    if not found:
        new.append(item)
    return new


def _delete(items: list[dict], automation_id: str) -> list[dict]:
    return [it for it in items if it.get("id") != automation_id]


def _make_item(data: dict) -> dict:
    return {
        "id": data.get("id"),
        "alias": data.get("name"),
        "description": data.get("description", ""),
        "trigger": data.get("trigger", []),
        "condition": data.get("condition", []),
        "action": data.get("action", []),
        "mode": data.get("mode", "single"),
    }


async def create_or_update(hass: HomeAssistant, data: dict) -> None:
    store = _store(hass)
    payload = await store.async_load() or {"items": []}
    items = payload.get("items", [])
    item = _make_item(data)
    payload["items"] = _upsert(items, item)
    await store.async_save(payload)


async def delete(hass: HomeAssistant, automation_id: str) -> None:
    store = _store(hass)
    payload = await store.async_load() or {"items": []}
    items = payload.get("items", [])
    payload["items"] = _delete(items, automation_id)
    await store.async_save(payload)


async def reload_automations(hass: HomeAssistant) -> None:
    await hass.services.async_call("automation", "reload", blocking=True)
