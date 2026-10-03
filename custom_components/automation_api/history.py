"""History endpoint backed by HA's recorder.

Wraps `homeassistant.components.recorder.history` so external clients can
query state-change history for any entity with an administrator token.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util


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
