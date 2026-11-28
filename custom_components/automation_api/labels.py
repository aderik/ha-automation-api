"""Labels: the tags of Settings → Areas, labels & zones.

Labels live in Home Assistant's own label registry, a core helper like the
entity and device registries. This module reads and changes them through
that registry. Which entities and devices carry a label is kept on the
entity and the device: see `update_entity` and `update_device` in `registry`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import label_registry as lr

FIELDS = ("name", "color", "icon", "description")


def _to_dict(label) -> dict[str, Any]:
    return {
        "label_id": label.label_id,
        "name": label.name,
        "color": label.color,
        "icon": label.icon,
        "description": label.description,
    }


def _fields(config: Any) -> dict:
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    if unknown := set(config) - set(FIELDS):
        raise ValueError(f"a label has {list(FIELDS)}, not {sorted(unknown)}")
    return config


def list_labels(hass: HomeAssistant) -> list[dict]:
    return [_to_dict(label) for label in lr.async_get(hass).async_list_labels()]


def create_label(hass: HomeAssistant, config: Any) -> dict:
    """Create a label; its id is derived from the name."""
    config = _fields(config)
    name = config.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a label needs a 'name'")
    others = {k: v for k, v in config.items() if k != "name"}
    # the registry refuses a name that is in use with a ValueError of its own
    return _to_dict(lr.async_get(hass).async_create(name.strip(), **others))


def update_label(hass: HomeAssistant, label_id: str, config: Any) -> dict | None:
    """Change the given fields of a label; None if there is none with that id."""
    registry = lr.async_get(hass)
    if registry.async_get_label(label_id) is None:
        return None
    return _to_dict(registry.async_update(label_id, **_fields(config)))


def delete_label(hass: HomeAssistant, label_id: str) -> bool:
    """Delete a label; Home Assistant takes it off every entity, device and
    area that carried it."""
    registry = lr.async_get(hass)
    if registry.async_get_label(label_id) is None:
        return False
    registry.async_delete(label_id)
    return True
