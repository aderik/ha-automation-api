from __future__ import annotations

from homeassistant.core import HomeAssistant


def normalize_payload(data: dict) -> dict:
    return {
        "id": data.get("automation_id") or data.get("id"),
        "name": data.get("name"),
        "description": data.get("description", ""),
        "trigger": data.get("trigger", []),
        "condition": data.get("condition", []),
        "action": data.get("action", []),
        "mode": data.get("mode", "single"),
    }


async def log(hass: HomeAssistant, message: str, level: str = "warning") -> None:
    await hass.services.async_call(
        "system_log",
        "write",
        {"message": f"[automation_api] {message}", "level": level},
        blocking=False,
    )
