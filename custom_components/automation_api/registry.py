"""Entity, device and config-entry registry management.

Wraps Home Assistant's internal registries so external clients can list,
inspect, mutate and delete entities/devices/integrations via REST. Used
primarily for cleaning up duplicate entities that integrations re-add on
restart (e.g. Tuya re-discovering devices already managed by ZHA).
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant


# --- Helpers -------------------------------------------------------------

def _enum_str(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


# --- Entity registry -----------------------------------------------------

def _entity_to_dict(entry) -> dict[str, Any]:
    return {
        "entity_id": entry.entity_id,
        "unique_id": entry.unique_id,
        "platform": entry.platform,
        "domain": entry.domain,
        "device_id": entry.device_id,
        "area_id": entry.area_id,
        "name": entry.name,
        "original_name": entry.original_name,
        "icon": entry.icon,
        "original_icon": entry.original_icon,
        "disabled_by": _enum_str(entry.disabled_by),
        "hidden_by": _enum_str(entry.hidden_by),
        "entity_category": _enum_str(entry.entity_category),
        "config_entry_id": entry.config_entry_id,
        "has_entity_name": getattr(entry, "has_entity_name", False),
    }


def _entity_registry(hass: HomeAssistant):
    from homeassistant.helpers import entity_registry as er
    return er.async_get(hass)


async def list_entities(
    hass: HomeAssistant,
    *,
    domain: str | None = None,
    platform: str | None = None,
    device_id: str | None = None,
    area_id: str | None = None,
    config_entry_id: str | None = None,
    disabled: bool | None = None,
) -> list[dict]:
    reg = _entity_registry(hass)
    out: list[dict] = []
    for ent in reg.entities.values():
        if domain and ent.domain != domain:
            continue
        if platform and ent.platform != platform:
            continue
        if device_id and ent.device_id != device_id:
            continue
        if area_id and ent.area_id != area_id:
            continue
        if config_entry_id and ent.config_entry_id != config_entry_id:
            continue
        if disabled is True and ent.disabled_by is None:
            continue
        if disabled is False and ent.disabled_by is not None:
            continue
        out.append(_entity_to_dict(ent))
    return out


async def get_entity(hass: HomeAssistant, entity_id: str) -> dict | None:
    reg = _entity_registry(hass)
    ent = reg.async_get(entity_id)
    return _entity_to_dict(ent) if ent else None


async def update_entity(
    hass: HomeAssistant, entity_id: str, changes: dict
) -> dict | None:
    from homeassistant.helpers.entity_registry import (
        RegistryEntryDisabler,
        RegistryEntryHider,
    )
    reg = _entity_registry(hass)
    if reg.async_get(entity_id) is None:
        return None
    kwargs: dict[str, Any] = {}
    for k in ("name", "icon", "area_id", "new_entity_id"):
        if k in changes:
            kwargs[k] = changes[k]
    if "disabled_by" in changes:
        v = changes["disabled_by"]
        kwargs["disabled_by"] = RegistryEntryDisabler.USER if v else None
    if "hidden_by" in changes:
        v = changes["hidden_by"]
        kwargs["hidden_by"] = RegistryEntryHider.USER if v else None
    new_id = kwargs.pop("new_entity_id", None)
    if new_id and new_id != entity_id:
        kwargs["new_entity_id"] = new_id
    new_entry = reg.async_update_entity(entity_id, **kwargs)
    return _entity_to_dict(new_entry)


async def delete_entity(hass: HomeAssistant, entity_id: str) -> bool:
    reg = _entity_registry(hass)
    if reg.async_get(entity_id) is None:
        return False
    reg.async_remove(entity_id)
    return True


# --- Device registry -----------------------------------------------------

def _device_to_dict(dev) -> dict[str, Any]:
    return {
        "id": dev.id,
        "name": dev.name,
        "name_by_user": dev.name_by_user,
        "manufacturer": dev.manufacturer,
        "model": dev.model,
        "sw_version": dev.sw_version,
        "hw_version": dev.hw_version,
        "area_id": dev.area_id,
        "identifiers": [list(i) for i in dev.identifiers],
        "connections": [list(c) for c in dev.connections],
        "config_entries": list(dev.config_entries),
        "disabled_by": _enum_str(dev.disabled_by),
        "via_device_id": dev.via_device_id,
    }


def _device_registry(hass: HomeAssistant):
    from homeassistant.helpers import device_registry as dr
    return dr.async_get(hass)


async def list_devices(
    hass: HomeAssistant,
    *,
    area_id: str | None = None,
    manufacturer: str | None = None,
    model: str | None = None,
    integration: str | None = None,
    config_entry_id: str | None = None,
    disabled: bool | None = None,
) -> list[dict]:
    reg = _device_registry(hass)
    integration_entries: set[str] | None = None
    if integration:
        integration_entries = {
            e.entry_id for e in hass.config_entries.async_entries(integration)
        }
    out: list[dict] = []
    for dev in reg.devices.values():
        if area_id and dev.area_id != area_id:
            continue
        if manufacturer and (dev.manufacturer or "").lower() != manufacturer.lower():
            continue
        if model and (dev.model or "").lower() != model.lower():
            continue
        if integration_entries is not None and not (dev.config_entries & integration_entries):
            continue
        if config_entry_id and config_entry_id not in dev.config_entries:
            continue
        if disabled is True and dev.disabled_by is None:
            continue
        if disabled is False and dev.disabled_by is not None:
            continue
        out.append(_device_to_dict(dev))
    return out


async def get_device(hass: HomeAssistant, device_id: str) -> dict | None:
    reg = _device_registry(hass)
    dev = reg.async_get(device_id)
    return _device_to_dict(dev) if dev else None


async def update_device(
    hass: HomeAssistant, device_id: str, changes: dict
) -> dict | None:
    from homeassistant.helpers.device_registry import DeviceEntryDisabler
    reg = _device_registry(hass)
    if reg.async_get(device_id) is None:
        return None
    kwargs: dict[str, Any] = {}
    for k in ("name_by_user", "area_id"):
        if k in changes:
            kwargs[k] = changes[k]
    if "disabled_by" in changes:
        v = changes["disabled_by"]
        kwargs["disabled_by"] = DeviceEntryDisabler.USER if v else None
    new = reg.async_update_device(device_id, **kwargs)
    return _device_to_dict(new)


async def delete_device(hass: HomeAssistant, device_id: str) -> bool:
    reg = _device_registry(hass)
    if reg.async_get(device_id) is None:
        return False
    reg.async_remove_device(device_id)
    return True


# --- Config entries (integrations) --------------------------------------

def _config_entry_to_dict(entry) -> dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "domain": entry.domain,
        "title": entry.title,
        "source": entry.source,
        "state": _enum_str(entry.state),
        "disabled_by": _enum_str(entry.disabled_by),
        "supports_unload": getattr(entry, "supports_unload", False),
        "supports_remove_device": getattr(entry, "supports_remove_device", False),
        "pref_disable_new_entities": getattr(entry, "pref_disable_new_entities", False),
        "pref_disable_polling": getattr(entry, "pref_disable_polling", False),
    }


async def list_config_entries(
    hass: HomeAssistant, *, domain: str | None = None
) -> list[dict]:
    entries = hass.config_entries.async_entries(domain)
    return [_config_entry_to_dict(e) for e in entries]


async def get_config_entry(hass: HomeAssistant, entry_id: str) -> dict | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    return _config_entry_to_dict(entry) if entry else None


async def disable_config_entry(
    hass: HomeAssistant, entry_id: str, *, disable: bool = True
) -> bool:
    from homeassistant.config_entries import ConfigEntryDisabler
    if hass.config_entries.async_get_entry(entry_id) is None:
        return False
    disabler = ConfigEntryDisabler.USER if disable else None
    await hass.config_entries.async_set_disabled_by(entry_id, disabler)
    return True


async def reload_config_entry(hass: HomeAssistant, entry_id: str) -> bool:
    if hass.config_entries.async_get_entry(entry_id) is None:
        return False
    await hass.config_entries.async_reload(entry_id)
    return True


async def remove_config_entry(hass: HomeAssistant, entry_id: str) -> bool:
    if hass.config_entries.async_get_entry(entry_id) is None:
        return False
    result = await hass.config_entries.async_remove(entry_id)
    return bool(result)
