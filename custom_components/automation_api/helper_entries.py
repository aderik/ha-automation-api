"""Helpers that Home Assistant keeps as config entries.

Template entities and `history_stats` sensors made in the UI are config
entries of their integration: Settings → Helpers runs the integration's
config flow to create one and its options flow to change one. This module
runs the same flows through Home Assistant's flow manager and answers their
steps from a config dict, so the integration validates everything and the
entity is live at once, without YAML and without a restart.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .registry import _enum_str

# A flow with more steps than this is not one of a helper
_MAX_STEPS = 10

_CONTEXT = {"source": SOURCE_USER, "show_advanced_options": True}


def _fields(result) -> set[str]:
    """The names of the fields a form step asks for."""
    schema = result.get("data_schema")
    return {str(getattr(key, "schema", key)) for key in (schema.schema if schema else ())}


async def _answer(flows, result, config: dict, choice: str | None = None):
    """Answer the steps of a flow from `config` until it creates its entry.

    A menu step is answered with `choice`, a form step with the fields of
    `config` it asks for. Returns the final result and the fields used. A
    flow that refuses is aborted and reported as a ValueError.
    """
    used: set[str] = set()
    for _ in range(_MAX_STEPS):
        kind = _enum_str(result["type"])
        if kind == "create_entry":
            return result, used
        if kind == "abort":
            raise ValueError(f"refused: {result.get('reason')}")
        flow_id = result["flow_id"]
        try:
            if kind == "menu":
                options = sorted(str(option) for option in result["menu_options"])
                if choice not in options:
                    raise ValueError(f"type must be one of {options}")
                answer = {"next_step_id": choice}
            elif kind == "form":
                if result.get("errors"):
                    raise ValueError(f"invalid config: {result['errors']}")
                answer = {k: v for k, v in config.items() if k in _fields(result)}
                used |= answer.keys()
            else:
                raise ValueError(f"cannot answer a '{kind}' step")
            result = await flows.async_configure(flow_id, answer)
        except (ValueError, vol.Invalid) as e:
            flows.async_abort(flow_id)
            raise ValueError(str(e)) from e
    flows.async_abort(result["flow_id"])
    raise ValueError("the flow did not finish")


def _to_dict(hass: HomeAssistant, entry) -> dict[str, Any]:
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "config": dict(entry.options),
        "entities": [entity.entity_id for entity in entities],
    }


def _entry(hass: HomeAssistant, domain: str, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    return entry if entry is not None and entry.domain == domain else None


def list_entries(hass: HomeAssistant, domain: str) -> list[dict]:
    return [_to_dict(hass, e) for e in hass.config_entries.async_entries(domain)]


def get_entry(hass: HomeAssistant, domain: str, entry_id: str) -> dict | None:
    entry = _entry(hass, domain, entry_id)
    return None if entry is None else _to_dict(hass, entry)


async def create_entry(
    hass: HomeAssistant, domain: str, config: dict, choice: str | None = None
) -> dict:
    """Create a helper with the config flow of `domain`.

    `choice` picks the kind of helper where the flow starts with a menu (the
    template type). A field no step of the flow asks for is an error, and
    then nothing is created.
    """
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    flows = hass.config_entries.flow
    result = await flows.async_init(domain, context=dict(_CONTEXT))
    result, used = await _answer(flows, result, config, choice)
    entry = result["result"]
    if unused := config.keys() - used:
        await hass.config_entries.async_remove(entry.entry_id)
        raise ValueError(
            f"{sorted(unused)} are not fields of this {domain} helper; "
            "nothing was created"
        )
    return _to_dict(hass, entry)


async def update_entry(
    hass: HomeAssistant, domain: str, entry_id: str, config: dict
) -> dict | None:
    """Replace the options of a helper with its options flow; None if there
    is no such helper. Optional fields left out of `config` are cleared, as
    when the form is saved with them empty."""
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    entry = _entry(hass, domain, entry_id)
    if entry is None:
        return None
    flows = hass.config_entries.options
    result = await flows.async_init(entry_id, context=dict(_CONTEXT))
    # Nothing is saved until the form is answered, so refuse up front
    if _enum_str(result["type"]) == "form" and (
        unknown := config.keys() - _fields(result)
    ):
        flows.async_abort(result["flow_id"])
        raise ValueError(
            f"{sorted(unknown)} cannot be changed; "
            f"the fields are {sorted(_fields(result))}"
        )
    await _answer(flows, result, config)
    return _to_dict(hass, entry)


async def delete_entry(hass: HomeAssistant, domain: str, entry_id: str) -> bool:
    if _entry(hass, domain, entry_id) is None:
        return False
    await hass.config_entries.async_remove(entry_id)
    return True
