from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .http import async_register_http
from .mcp import async_register_mcp


async def async_setup(hass: HomeAssistant, config: dict):
    hass.data.setdefault(DOMAIN, {})
    async_register_http(hass)
    async_register_mcp(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    hass.data.setdefault(DOMAIN, {})["entry"] = entry
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    if DOMAIN in hass.data:
        hass.data[DOMAIN].pop("entry", None)
    return True
