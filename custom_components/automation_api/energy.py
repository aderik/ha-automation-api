"""The energy dashboard settings, through the energy integration's commands.

Settings → Dashboards → Energy reads with `energy/get_prefs`, validates with
`energy/validate` and saves with `energy/save_prefs`. This module sends the
same commands as the caller (see `ha_client`); the energy integration checks
what is saved and fills in what it derives.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.core import HomeAssistant

from .ha_client import connect as _connect

KEYS = ("energy_sources", "device_consumption", "device_consumption_water")


class StalePrefs(ValueError):
    """The settings changed since the read a change was based on."""

    def __init__(self, current_hash: str) -> None:
        super().__init__(
            "the energy settings changed since config_hash was read; read "
            "them again and redo the change"
        )
        self.current_hash = current_hash


def prefs_hash(prefs: Any) -> str:
    """Fingerprint of the stored prefs, to refuse a save based on a stale read."""
    text = json.dumps(prefs, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _prefs(send) -> dict:
    try:
        return await send("energy/get_prefs")
    except ValueError as e:
        if str(e).endswith("(not_found)"):  # nothing is set up yet
            return {key: [] for key in KEYS}
        raise


async def read(hass: HomeAssistant, token: str | None) -> dict[str, Any]:
    """The settings, what the energy page finds wrong with them, and what a
    change needs to know."""
    async with _connect(hass, token, ValueError) as send:
        prefs = await _prefs(send)
        validation = await send("energy/validate")
        info = await send("energy/info")
    forecast_domains = set(info.get("solar_forecast_domains") or [])
    return {
        "prefs": prefs,
        "config_hash": prefs_hash(prefs),
        "validation": validation,
        # prices are in this currency (Settings > General)
        "currency": hass.config.currency,
        # config entries usable as config_entry_solar_forecast
        "solar_forecast_entries": [
            {"entry_id": e.entry_id, "domain": e.domain, "title": e.title}
            for e in hass.config_entries.async_entries()
            if e.domain in forecast_domains
        ],
    }


async def save(
    hass: HomeAssistant,
    token: str | None,
    update: dict,
    based_on: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Replace the lists in `update`; with `dry_run`, only tell what would
    change. The energy integration validates the lists when it saves them."""
    async with _connect(hass, token, ValueError) as send:
        current = await _prefs(send)
        current_hash = prefs_hash(current)
        if based_on is not None and based_on != current_hash:
            raise StalePrefs(current_hash)
        changed = sorted(key for key in update if update[key] != current.get(key))
        if dry_run:
            # Home Assistant fills in derived fields (power sensors) on save
            return {
                "status": "dry_run",
                "changed": changed,
                "prefs": {**current, **update},
                "config_hash": current_hash,
            }
        saved = await send("energy/save_prefs", **update)
    return {
        "status": "ok",
        "changed": changed,
        "prefs": saved,
        "config_hash": prefs_hash(saved),
    }
