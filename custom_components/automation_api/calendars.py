"""Calendars: read their events and change them.

A calendar is an entity of some integration (the local calendar, Google
Calendar, CalDAV, ...). Events are read with Home Assistant's
`/api/calendars/<entity_id>` endpoint and created, changed and deleted with
the `calendar/event/*` WebSocket commands, which the calendar panel uses;
both are sent as the caller (see `ha_client`). The integration behind the
calendar does the work and refuses what it can't do.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import quote, urlencode

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .ha_client import connect as _connect, request as _request
from .stats import parse_time

# The fields of an event; start, end and summary are required
EVENT_FIELDS = ("summary", "start", "end", "description", "location", "rrule")

# Which occurrences of a recurring event a change applies to: this one, or
# with "THISANDFUTURE" this and the later ones
_TARGET = ("recurrence_id", "recurrence_range")


def _entity(entity_id: str) -> str:
    if not isinstance(entity_id, str) or not entity_id.startswith("calendar."):
        raise ValueError("entity_id must be a calendar entity")
    return entity_id


async def list_events(
    hass: HomeAssistant,
    token: str | None,
    entity_id: str,
    *,
    start: str | None = None,
    end: str | None = None,
    days: Any = None,
) -> dict | None:
    """The events of a calendar in a window, by default the coming 7 days;
    None if there is no such calendar."""
    if hass.states.get(_entity(entity_id)) is None:
        return None
    start_time = parse_time(start, "start") if start else dt_util.utcnow()
    if end:
        end_time = parse_time(end, "end")
    else:
        try:
            end_time = start_time + timedelta(days=float(days) if days else 7)
        except (TypeError, ValueError):
            raise ValueError("'days' must be a number") from None
    if end_time <= start_time:
        raise ValueError("'end' must be after 'start'")
    window = {"start": start_time.isoformat(), "end": end_time.isoformat()}
    status, answer = await _request(
        hass,
        token,
        ValueError,
        "GET",
        f"/api/calendars/{quote(_entity(entity_id), safe='')}?{urlencode(window)}",
    )
    if status == 404:
        return None
    if status >= 400:
        message = answer.get("message") if isinstance(answer, dict) else answer
        raise ValueError(f"{message} (HTTP {status})")
    return {
        "start": dt_util.as_local(start_time).isoformat(),
        "end": dt_util.as_local(end_time).isoformat(),
        "events": [_read(event) for event in answer],
    }


def _read(event: dict) -> dict:
    """An event as it is read: what is set, with `start` and `end` as the
    plain date or time `create_event` takes."""
    found = {k: v for k, v in event.items() if v is not None}
    for key in ("start", "end"):
        if isinstance(value := found.get(key), dict):
            found[key] = value.get("dateTime") or value.get("date")
    return found


def _event(config: Any) -> dict:
    if not isinstance(config, dict):
        raise ValueError("an event is an object")
    if unknown := set(config) - set(EVENT_FIELDS):
        raise ValueError(f"an event has {list(EVENT_FIELDS)}, not {sorted(unknown)}")
    if missing := [f for f in ("summary", "start", "end") if not config.get(f)]:
        raise ValueError(f"an event needs {missing}")
    # the websocket commands call the two times as iCalendar does
    names = {"start": "dtstart", "end": "dtend"}
    return {names.get(k, k): v for k, v in config.items() if v is not None}


def _occurrence(target: dict) -> dict:
    return {k: target[k] for k in _TARGET if target.get(k)}


async def create_event(
    hass: HomeAssistant, token: str | None, entity_id: str, config: Any
) -> None:
    """Add an event. `start` and `end` are both dates (an all-day event; the
    end date is the day after the last day) or both times."""
    async with _connect(hass, token, ValueError) as send:
        await send("calendar/event/create", entity_id=_entity(entity_id), event=_event(config))


async def update_event(
    hass: HomeAssistant,
    token: str | None,
    entity_id: str,
    uid: str,
    config: Any,
    **target: Any,
) -> None:
    """Replace an event, named by its `uid`: the whole event is sent, not
    only what changes."""
    async with _connect(hass, token, ValueError) as send:
        await send(
            "calendar/event/update",
            entity_id=_entity(entity_id),
            uid=uid,
            event=_event(config),
            **_occurrence(target),
        )


async def delete_event(
    hass: HomeAssistant, token: str | None, entity_id: str, uid: str, **target: Any
) -> None:
    """Delete an event, named by its `uid`."""
    async with _connect(hass, token, ValueError) as send:
        await send(
            "calendar/event/delete",
            entity_id=_entity(entity_id),
            uid=uid,
            **_occurrence(target),
        )
