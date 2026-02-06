from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant


async def _read_storage(hass: HomeAssistant) -> dict:
    path = hass.config.path(".storage/automation")
    def _load():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {"version": 1, "key": "automation", "data": {"items": []}}
    return await hass.async_add_executor_job(_load)


async def _write_storage(hass: HomeAssistant, payload: dict) -> None:
    path = hass.config.path(".storage/automation")
    def _dump():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    await hass.async_add_executor_job(_dump)


def _ensure_structure(payload: dict) -> dict:
    if not isinstance(payload, dict):
        payload = {"version": 1, "key": "automation", "data": {"items": []}}
    payload.setdefault("version", 1)
    payload.setdefault("key", "automation")
    data = payload.setdefault("data", {})
    data.setdefault("items", [])
    return payload


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
    payload = _ensure_structure(await _read_storage(hass))
    items = payload["data"]["items"]
    item = _make_item(data)
    payload["data"]["items"] = _upsert(items, item)
    await _write_storage(hass, payload)


async def delete(hass: HomeAssistant, automation_id: str) -> None:
    payload = _ensure_structure(await _read_storage(hass))
    items = payload["data"]["items"]
    payload["data"]["items"] = _delete(items, automation_id)
    await _write_storage(hass, payload)


async def reload_automations(hass: HomeAssistant) -> None:
    await hass.services.async_call("automation", "reload", blocking=True)
