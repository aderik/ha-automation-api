from __future__ import annotations

from datetime import datetime, timezone
import os

from homeassistant.core import HomeAssistant

from .const import LOG_FILE


def normalize_payload(data: dict) -> dict:
    return {
        "id": data.get("automation_id") or data.get("id"),
        "name": data.get("name") or "(unnamed)",
        "description": data.get("description", ""),
        "trigger": data.get("trigger", []),
        "condition": data.get("condition", []),
        "action": data.get("action", []),
        "mode": data.get("mode", "single"),
    }


def _append_log(path: str, line: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


async def log(hass: HomeAssistant, message: str, level: str = "warning") -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"{timestamp} [{level}] {message}\n"
    path = hass.config.path(LOG_FILE)
    await hass.async_add_executor_job(_append_log, path, line)
