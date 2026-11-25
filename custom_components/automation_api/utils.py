from __future__ import annotations

from datetime import datetime, timezone
import os

from homeassistant.core import HomeAssistant

from .const import LOG_FILE


def _append_log(path: str, line: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


async def log(hass: HomeAssistant, message: str, level: str = "warning") -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"{timestamp} [{level}] {message}\n"
    path = hass.config.path(LOG_FILE)
    await hass.async_add_executor_job(_append_log, path, line)


# Domains whose YAML config can be reloaded at runtime via <domain>.reload.
RELOADABLE_DOMAINS: set[str] = {
    "automation",
    "input_boolean",
    "input_button",
    "input_datetime",
    "input_number",
    "input_select",
    "input_text",
    "scene",
    "script",
    "template",
}


async def reload_domain(hass: HomeAssistant, domain: str) -> bool:
    if domain not in RELOADABLE_DOMAINS:
        return False
    try:
        await hass.services.async_call(domain, "reload", {}, blocking=True)
        return True
    except Exception:
        return False


async def reload_all(hass: HomeAssistant) -> bool:
    try:
        await hass.services.async_call(
            "homeassistant", "reload_all", {}, blocking=True
        )
        return True
    except Exception:
        return False


async def restart(hass: HomeAssistant) -> None:
    await hass.services.async_call("homeassistant", "restart", {}, blocking=True)
