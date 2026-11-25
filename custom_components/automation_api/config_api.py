"""Automations and scripts, through the config API of their editors.

Home Assistant's automation and script editors save through
`/api/config/automation/config/<id>` and `/api/config/script/config/<id>`.
Those endpoints validate the config, write `automations.yaml` or
`scripts.yaml` themselves, reload what changed and clean up the entity
registry. This module sends them the same requests, as the caller (see
`ha_client`); it never touches the files.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from homeassistant.core import HomeAssistant

from .ha_client import request as _request


async def _call(
    hass: HomeAssistant,
    token: str | None,
    method: str,
    domain: str,
    key: str,
    config: dict | None = None,
) -> Any:
    """The answer of the config API, or None where it has no such item."""
    status, answer = await _request(
        hass,
        token,
        ValueError,
        method,
        f"/api/config/{domain}/config/{quote(key, safe='')}",
        config,
    )
    if status == 404:
        return None
    if status >= 400:
        message = answer.get("message") if isinstance(answer, dict) else answer
        raise ValueError(f"{message} (HTTP {status})")
    return answer


async def get(
    hass: HomeAssistant, token: str | None, domain: str, key: str
) -> dict | None:
    """The stored config of an automation (by id) or script (by object id)."""
    return await _call(hass, token, "GET", domain, key)


async def save(
    hass: HomeAssistant, token: str | None, domain: str, key: str, config: dict
) -> None:
    """Create or replace an automation or script, loaded when this returns."""
    await _call(hass, token, "POST", domain, key, config)
    # The config API reloads in the background and answers before that is
    # done. Wait for a reload here, so that what was saved can be triggered
    # or called as soon as this returns.
    await hass.services.async_call(
        domain, "reload", {"id": key} if domain == "automation" else {}, blocking=True
    )


async def delete(hass: HomeAssistant, token: str | None, domain: str, key: str) -> bool:
    """Remove an automation or script; False if there is none with that key."""
    # The config API answers a missing key with a plain 400 on DELETE
    if await get(hass, token, domain, key) is None:
        return False
    await _call(hass, token, "DELETE", domain, key)
    return True
