"""What happened: state history, traces, the logbook and errors.

State history wraps `homeassistant.components.recorder.history`. The rest is
read with the WebSocket commands the Home Assistant UI itself uses, sent as
the caller (see `ha_client`): the traces of automation and script runs, the
logbook and the system log. Long-term statistics are in `stats`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .ha_client import connect as _connect

TRACE_DOMAINS = ("automation", "script")


def _state_to_dict(item: State | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    return {
        "state": item.state,
        "last_changed": item.last_changed.isoformat() if item.last_changed else None,
        "last_updated": item.last_updated.isoformat() if item.last_updated else None,
    }


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt_util.as_utc(dt)
    return dt_util.as_utc(dt)


def parse_time_window(
    *,
    start: str | None,
    end: str | None,
    hours: str | None,
    days: str | None,
) -> tuple[datetime, datetime]:
    end_time = dt_util.parse_datetime(end) if end else dt_util.utcnow()
    if end_time is None:
        raise ValueError(f"could not parse 'end': {end!r}")

    if start:
        start_time = dt_util.parse_datetime(start)
        if start_time is None:
            raise ValueError(f"could not parse 'start': {start!r}")
    elif hours:
        try:
            start_time = end_time - timedelta(hours=float(hours))
        except ValueError as e:
            raise ValueError(f"invalid 'hours': {hours!r}") from e
    elif days:
        try:
            start_time = end_time - timedelta(days=float(days))
        except ValueError as e:
            raise ValueError(f"invalid 'days': {days!r}") from e
    else:
        start_time = end_time - timedelta(hours=24)

    return _ensure_utc(start_time), _ensure_utc(end_time)


async def fetch_history(
    hass: HomeAssistant,
    *,
    entity_ids: list[str],
    start_time: datetime,
    end_time: datetime,
    significant: bool = False,
    minimal: bool = True,
    no_attributes: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import (
        get_significant_states,
        state_changes_during_period,
    )

    rec = get_instance(hass)

    def _fetch():
        if significant:
            return get_significant_states(
                hass,
                start_time,
                end_time,
                entity_ids=entity_ids,
                minimal_response=minimal,
                no_attributes=no_attributes,
            )
        out: dict[str, list] = {}
        for eid in entity_ids:
            data = state_changes_during_period(
                hass,
                start_time,
                end_time,
                entity_id=eid,
                no_attributes=no_attributes,
            )
            out[eid] = data.get(eid, [])
        return out

    raw = await rec.async_add_executor_job(_fetch)
    return {eid: [_state_to_dict(s) for s in states] for eid, states in raw.items()}


def _iso(seconds: float | None) -> str | None:
    """An ISO 8601 time for seconds since the epoch."""
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


# --- Traces of automation and script runs ---------------------------------

def trace_item_id(hass: HomeAssistant, domain: str, item: str) -> str:
    """The id Home Assistant keeps traces under: for an automation the `id`
    of its config, for a script its object id. `item` may be that id, or the
    entity id with or without its domain."""
    if domain not in TRACE_DOMAINS:
        raise ValueError(f"domain must be one of {list(TRACE_DOMAINS)}")
    object_id = item.removeprefix(f"{domain}.")
    if domain == "script":
        return object_id
    state = hass.states.get(f"automation.{object_id}")
    return (state.attributes.get("id") if state else None) or object_id


async def list_traces(
    hass: HomeAssistant, token: str | None, domain: str, item: str | None = None
) -> list[dict]:
    """The stored runs, newest first: of one automation or script, or of all."""
    if domain not in TRACE_DOMAINS:
        raise ValueError(f"domain must be one of {list(TRACE_DOMAINS)}")
    fields = {"item_id": trace_item_id(hass, domain, item)} if item else {}
    async with _connect(hass, token, ValueError) as send:
        found = await send("trace/list", domain=domain, **fields)
    return sorted(found, key=lambda t: t["timestamp"]["start"], reverse=True)


async def get_trace(
    hass: HomeAssistant, token: str | None, domain: str, item: str, run_id: str
) -> dict | None:
    """One run, step by step, as the trace view of the editor shows it; None
    if Home Assistant no longer has it."""
    item_id = trace_item_id(hass, domain, item)
    async with _connect(hass, token, ValueError) as send:
        try:
            return await send("trace/get", domain=domain, item_id=item_id, run_id=run_id)
        except ValueError as e:
            if str(e).endswith("(not_found)"):
                return None
            raise


# --- The logbook and the system log ---------------------------------------

async def fetch_logbook(
    hass: HomeAssistant,
    token: str | None,
    *,
    start_time: datetime,
    end_time: datetime,
    entity_ids: list[str] | None = None,
) -> list[dict]:
    """Logbook entries, oldest first: what changed, when, and what caused it."""
    async with _connect(hass, token, ValueError) as send:
        found = await send(
            "logbook/get_events",
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            **({"entity_ids": entity_ids} if entity_ids else {}),
        )
    return [{**event, "when": _iso(event.get("when"))} for event in found]


async def fetch_system_log(
    hass: HomeAssistant,
    token: str | None,
    *,
    level: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """The errors and warnings of Settings → System → Logs, newest first.
    Home Assistant keeps the latest ones since its start and counts repeats."""
    async with _connect(hass, token, ValueError) as send:
        found = await send("system_log/list")
    wanted = (search or "").lower()
    entries = []
    for entry in found:
        message = "\n".join(entry.get("message") or [])
        exception = entry.get("exception") or None
        if level and entry.get("level", "").lower() != level.lower():
            continue
        text = f"{entry.get('name')} {message} {exception or ''}".lower()
        if wanted not in text:
            continue
        source = entry.get("source") or []
        entries.append(
            {
                "level": entry.get("level"),
                "logger": entry.get("name"),
                "message": message,
                "exception": exception,
                "source": ":".join(str(part) for part in source),
                "count": entry.get("count"),
                "first_occurred": _iso(entry.get("first_occurred")),
                "last_occurred": _iso(entry.get("timestamp")),
            }
        )
    return sorted(entries, key=lambda e: e["last_occurred"] or "", reverse=True)
