from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.yaml import load_yaml, dump

AUTOMATION_CONFIG_PATH = "automations.yaml"


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


def _upsert(items: list[dict], item: dict) -> list[dict]:
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


def _load_yaml(path: str) -> list[dict]:
    data = load_yaml(path) or []
    if isinstance(data, dict):
        return data.get("automation", [])
    return data


def _write_yaml(path: str, items: list[dict]) -> None:
    write_utf8_file_atomic(path, dump(items))


async def create_or_update(hass: HomeAssistant, data: dict) -> None:
    path = hass.config.path(AUTOMATION_CONFIG_PATH)
    item = _make_item(data)

    def _update():
        items = _load_yaml(path)
        items = _upsert(items, item)
        _write_yaml(path, items)

    await hass.async_add_executor_job(_update)


async def delete(hass: HomeAssistant, automation_id: str) -> None:
    path = hass.config.path(AUTOMATION_CONFIG_PATH)

    def _update():
        items = _load_yaml(path)
        items = _delete(items, automation_id)
        _write_yaml(path, items)

    await hass.async_add_executor_job(_update)


async def reload_automations(hass: HomeAssistant, automation_id: str | None = None) -> None:
    data = {"id": automation_id} if automation_id else None
    await hass.services.async_call("automation", "reload", data, blocking=True)
