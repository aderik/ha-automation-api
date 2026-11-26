"""Long-term statistics: read them, and repair them.

The recorder keeps an aggregate per hour for every sensor with a state
class, long after the detailed history is purged. Everything here goes
through the recorder's own WebSocket commands, sent as the caller (see
`ha_client`): the ones Developer Tools → Statistics and the energy dashboard
use.

Times are given, and returned, in the time zone of Home Assistant, with
their offset. A time without an offset is local time.

The two tools that write, `adjust_sum` and `import_rows`, only say what they
would do unless `dry_run` is false.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .ha_client import connect as _connect

PERIODS = ("5minute", "hour", "day", "week", "month", "year")
TYPES = ("change", "sum", "state", "mean", "min", "max", "last_reset")
DEFAULT_TYPES = ("change", "sum", "state")

# Seconds a period spans at least, to size an answer before asking for it
_SECONDS = {
    "5minute": 300,
    "hour": 3600,
    "day": 86400,
    "week": 7 * 86400,
    "month": 28 * 86400,
    "year": 365 * 86400,
}

DEFAULT_MAX_ROWS = 2000

# ponytail: the recorder has no paging, so a page is cut from the whole window
# after fetching it; beyond this many rows the window is refused instead.
_FETCH_LIMIT = 200_000

# How long to wait for the recorder to carry out a change before reading back
_SETTLE = 20
_POLL = 0.5

HOUR = timedelta(hours=1)


def parse_time(value: Any, name: str) -> datetime:
    """An aware time from an ISO string; without an offset it is local time,
    and a date alone is local midnight."""
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    if parsed is None and isinstance(value, str):
        date = dt_util.parse_date(value)
        parsed = None if date is None else datetime(date.year, date.month, date.day)
    if parsed is None:
        raise ValueError(f"could not parse '{name}': {value!r}")
    return dt_util.as_utc(parsed)


# The window read when none is asked for
_DEFAULT_WINDOW = {
    "5minute": timedelta(days=1),
    "hour": timedelta(days=1),
    "day": timedelta(days=30),
    "week": timedelta(weeks=26),
    "month": timedelta(days=365),
    "year": timedelta(days=5 * 365),
}


def _floor(time: datetime, period: str) -> datetime:
    """The start of the period `time` lies in, on the local clock.

    The recorder answers a window that starts halfway a period with a first
    row over the rest of that period only, which reads as a too low total.
    """
    local = dt_util.as_local(time).replace(second=0, microsecond=0)
    if period == "5minute":
        local = local.replace(minute=local.minute - local.minute % 5)
    elif period == "hour":
        local = local.replace(minute=0)
    else:
        local = local.replace(hour=0, minute=0)
        if period == "week":
            local -= timedelta(days=local.weekday())
        elif period == "month":
            local = local.replace(day=1)
        elif period == "year":
            local = local.replace(month=1, day=1)
    return dt_util.as_utc(local)


def window(
    period: str,
    *,
    start: str | None = None,
    end: str | None = None,
    hours: Any = None,
    days: Any = None,
) -> tuple[datetime, datetime]:
    """The window a request asks for, its start moved back to the start of
    the period it lies in."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of {list(PERIODS)}")
    end_time = parse_time(end, "end") if end else dt_util.utcnow()
    try:
        if start:
            start_time = parse_time(start, "start")
        elif hours:
            start_time = end_time - timedelta(hours=float(hours))
        elif days:
            start_time = end_time - timedelta(days=float(days))
        else:
            start_time = end_time - _DEFAULT_WINDOW[period]
    except (TypeError, ValueError) as e:
        if "could not parse" in str(e):
            raise
        raise ValueError("'hours' and 'days' must be numbers") from e
    return _floor(start_time, period), end_time


def _local(ms: float) -> str:
    """A recorder timestamp (epoch milliseconds) as local ISO time."""
    return dt_util.as_local(dt_util.utc_from_timestamp(ms / 1000)).isoformat()


def _rounded(value: Any) -> Any:
    return round(value, 3) if isinstance(value, float) else value


def as_list(value: Any, name: str) -> list[str]:
    """A list from a list or a comma-separated string."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"'{name}' must be a list or comma-separated")
    return [v.strip() for v in value if v.strip()]


async def _metadata(send, statistic_ids: list[str] | None) -> list[dict]:
    return await send(
        "recorder/get_statistics_metadata",
        **({"statistic_ids": statistic_ids} if statistic_ids else {}),
    )


async def _rows(
    send,
    statistic_ids: list[str],
    start: datetime,
    end: datetime,
    period: str,
    types: list[str],
    units: dict | None = None,
) -> dict[str, list[dict]]:
    return await send(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=statistic_ids,
        period=period,
        types=types,
        **({"units": units} if units else {}),
    )


# --- Reading ---------------------------------------------------------------

async def list_statistics(
    hass: HomeAssistant,
    token: str | None,
    *,
    search: str | None = None,
    kind: str | None = None,
) -> list[dict]:
    """The statistics the recorder keeps; `kind` is "mean" or "sum"."""
    if kind not in (None, "mean", "sum"):
        raise ValueError("type must be 'mean' or 'sum'")
    async with _connect(hass, token, ValueError) as send:
        found = await send(
            "recorder/list_statistic_ids", **({"statistic_type": kind} if kind else {})
        )
    wanted = (search or "").lower()
    return [
        {
            "statistic_id": item["statistic_id"],
            "name": item.get("name"),
            "unit": item.get("display_unit_of_measurement"),
            "unit_class": item.get("unit_class"),
            "source": item.get("source"),
            "has_sum": item.get("has_sum"),
            "mean_type": item.get("mean_type"),
        }
        for item in found
        if wanted in item["statistic_id"].lower()
        or wanted in (item.get("name") or "").lower()
    ]


async def get_metadata(
    hass: HomeAssistant, token: str | None, statistic_ids: list[str] | None = None
) -> list[dict]:
    """The metadata the recorder stores per statistic, as it gives it."""
    async with _connect(hass, token, ValueError) as send:
        return await _metadata(send, statistic_ids)


async def update_unit(
    hass: HomeAssistant,
    token: str | None,
    statistic_id: str,
    unit: str | None,
    unit_class: str | None = None,
) -> None:
    """Change the unit a statistic is stored in and keep its history, as
    Developer Tools → Statistics → Fix does; answers when the recorder is done."""
    async with _connect(hass, token, ValueError) as send:
        await send(
            "recorder/update_statistics_metadata",
            statistic_id=statistic_id,
            unit_of_measurement=unit,
            **({} if unit_class is None else {"unit_class": unit_class}),
        )


async def get_statistics(
    hass: HomeAssistant,
    token: str | None,
    *,
    statistic_ids: list[str],
    start: datetime,
    end: datetime,
    period: str = "day",
    types: list[str] | None = None,
    units: dict | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Statistics as a table per statistic id, a page of at most `max_rows`
    rows over all of them."""
    if not statistic_ids:
        raise ValueError("missing statistic_ids")
    if period not in PERIODS:
        raise ValueError(f"period must be one of {list(PERIODS)}")
    types = list(types or DEFAULT_TYPES)
    if unknown := [t for t in types if t not in TYPES]:
        raise ValueError(f"unknown types {unknown}; choose from {list(TYPES)}")
    if units is not None and not isinstance(units, dict):
        raise ValueError("'units' maps a unit class to a unit, e.g. {'energy': 'Wh'}")
    if max_rows < 1:
        raise ValueError("max_rows must be at least 1")
    estimate = (end - start).total_seconds() / _SECONDS[period] * len(statistic_ids)
    if estimate > _FETCH_LIMIT:
        raise ValueError(
            f"that window holds about {int(estimate)} rows; use a shorter "
            "window, a coarser period or fewer statistics"
        )
    async with _connect(hass, token, ValueError) as send:
        found = await _rows(send, statistic_ids, start, end, period, types, units)

    # A page holds whole periods: the first `fit` of them, for every statistic
    starts = sorted({row["start"] for rows in found.values() for row in rows})
    fit = max(1, max_rows // len(statistic_ids))
    cut = starts[fit] if len(starts) > fit else None
    columns = ["start", *types]
    result: dict[str, Any] = {
        "period": period,
        "start": dt_util.as_local(start).isoformat(),
        "end": dt_util.as_local(end).isoformat(),
        "statistics": {
            statistic_id: {
                "columns": columns,
                "rows": [
                    [_local(row["start"]), *(_rounded(row.get(t)) for t in types)]
                    for row in rows
                    if cut is None or row["start"] < cut
                ],
            }
            for statistic_id, rows in found.items()
        },
        "truncated": cut is not None,
    }
    if cut is not None:
        result["next_start"] = _local(cut)
    return result


# --- Writing ---------------------------------------------------------------

async def _one(send, statistic_id: str) -> dict:
    """The metadata of a statistic that has to exist."""
    found = await _metadata(send, [statistic_id])
    if not found:
        raise ValueError(f"there is no statistic '{statistic_id}'")
    return found[0]


def _in_unit(metadata: dict, unit: str | None) -> dict | None:
    """The `units` that make the recorder answer in `unit`."""
    unit_class = metadata.get("unit_class")
    return {unit_class: unit} if unit_class and unit else None


def _unit_note(metadata: dict) -> dict:
    """A warning for a statistic that is stored in another unit than it is
    shown in: rows are imported in the stored unit, without conversion."""
    stored = metadata.get("statistics_unit_of_measurement")
    shown = metadata.get("display_unit_of_measurement")
    if stored == shown:
        return {}
    return {
        "unit_note": (
            f"this statistic is stored in {stored} but shown in {shown}. The "
            f"rows to import must be in {stored}; read the current values in "
            f"that unit with get_statistics, units "
            f"{{'{metadata.get('unit_class')}': '{stored}'}}"
        )
    }


def _table(rows: list[dict], columns: tuple[str, ...]) -> dict:
    return {
        "columns": ["start", *columns],
        "rows": [[_local(r["start"]), *(_rounded(r.get(c)) for c in columns)] for r in rows],
    }


async def _settled(read, done) -> tuple[list[dict], bool]:
    """Read until the recorder, which works through a queue, has carried the
    change out; returns the last read and whether `done` saw it."""
    waited = 0.0
    while True:
        rows = await read()
        if done(rows) or waited >= _SETTLE:
            return rows, done(rows)
        await asyncio.sleep(_POLL)
        waited += _POLL


async def adjust_sum(
    hass: HomeAssistant,
    token: str | None,
    *,
    statistic_id: str,
    start_time: str,
    adjustment: float,
    unit: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Add `adjustment` to the `sum` of a statistic from an hour on."""
    start = parse_time(start_time, "start_time")
    if start.minute or start.second or start.microsecond:
        raise ValueError("start_time must be the start of an hour")
    if isinstance(adjustment, bool) or not isinstance(adjustment, (int, float)):
        raise ValueError("adjustment must be a number")
    columns = ("state", "sum", "change")
    async with _connect(hass, token, ValueError) as send:
        metadata = await _one(send, statistic_id)
        if not metadata.get("has_sum"):
            raise ValueError(f"'{statistic_id}' has no sum to adjust")
        # The unit the statistic is shown in, as the adjust dialog of
        # Developer Tools does; the recorder converts to the stored unit
        unit = (
            unit
            or metadata.get("display_unit_of_measurement")
            or metadata.get("statistics_unit_of_measurement")
        )

        async def read() -> list[dict]:
            found = await _rows(
                send, [statistic_id], start - 2 * HOUR, start + 4 * HOUR, "hour",
                list(columns), _in_unit(metadata, unit),
            )
            return found.get(statistic_id, [])

        before = await read()
        first = start.timestamp() * 1000
        # From the hour on every sum shifts; only that hour's own change does
        expected = [
            {
                **row,
                "sum": None if row.get("sum") is None else row["sum"] + adjustment,
                "change": row.get("change")
                if row["start"] != first or row.get("change") is None
                else row["change"] + adjustment,
            }
            if row["start"] >= first
            else row
            for row in before
        ]
        result: dict[str, Any] = {
            "statistic_id": statistic_id,
            "start_time": dt_util.as_local(start).isoformat(),
            "adjustment": adjustment,
            "unit": unit,
            "dry_run": dry_run,
            "before": _table(before, columns),
        }
        if dry_run:
            return {**result, "after_calculated": _table(expected, columns)}

        await send(
            "recorder/adjust_sum_statistics",
            statistic_id=statistic_id,
            start_time=start.isoformat(),
            adjustment=adjustment,
            adjustment_unit_of_measurement=unit,
        )
        sums = [round(r["sum"], 3) for r in expected if r.get("sum") is not None]
        after, applied = await _settled(
            read, lambda rows: [round(r["sum"], 3) for r in rows if r.get("sum") is not None] == sums
        )
        # Without an hour from start_time on in view there is nothing to see
        seen = any(r["start"] >= first and r.get("sum") is not None for r in before)
        return {
            **result,
            "after": _table(after, columns),
            "applied": applied if seen else None,
        }


def _check_rows(stats: Any) -> list[dict]:
    """The rows to import, validated, with their `start` as an aware time."""
    if not isinstance(stats, list) or not stats:
        raise ValueError("'stats' must be a list of {start, state?, sum?}")
    rows: list[dict] = []
    for item in stats:
        if not isinstance(item, dict) or "start" not in item:
            raise ValueError("every row needs a 'start'")
        if unknown := set(item) - {"start", "state", "sum"}:
            raise ValueError(f"a row holds start, state and sum, not {sorted(unknown)}")
        if "state" not in item and "sum" not in item:
            raise ValueError(f"the row at {item['start']} has neither state nor sum")
        for key in ("state", "sum"):
            value = item.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"'{key}' at {item['start']} must be a number")
        start = parse_time(item["start"], "start")
        if start.minute or start.second or start.microsecond:
            raise ValueError(f"{item['start']} is not the start of an hour")
        if rows and start <= rows[-1]["start"]:
            raise ValueError(f"the rows must be in order of time; {item['start']} is not")
        last_sum = next((r["sum"] for r in reversed(rows) if "sum" in r), None)
        if "sum" in item and last_sum is not None and item["sum"] < last_sum:
            raise ValueError(f"the sum decreases at {item['start']}")
        rows.append({**item, "start": start})
    return rows


async def import_rows(
    hass: HomeAssistant,
    token: str | None,
    *,
    statistic_id: str,
    stats: Any,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Write hourly rows of an existing statistic, replacing those hours."""
    rows = _check_rows(stats)
    async with _connect(hass, token, ValueError) as send:
        stored = await _one(send, statistic_id)
        # What the recorder stores goes back as it is; values are in its unit
        metadata = {
            "statistic_id": statistic_id,
            "source": stored["source"],
            "name": stored.get("name"),
            "has_sum": stored["has_sum"],
            "unit_of_measurement": stored.get("statistics_unit_of_measurement"),
            "unit_class": stored.get("unit_class"),
            "mean_type": stored.get("mean_type", 0),
        }
        first, last = rows[0]["start"], rows[-1]["start"]

        async def read() -> list[dict]:
            found = await _rows(
                send, [statistic_id], first, last + 2 * HOUR, "hour", ["state", "sum"],
                _in_unit(stored, metadata["unit_of_measurement"]),
            )
            return found.get(statistic_id, [])

        old = {row["start"]: row for row in await read()}
        key = lambda row: row["start"].timestamp() * 1000  # noqa: E731
        result: dict[str, Any] = {
            "statistic_id": statistic_id,
            "unit": metadata["unit_of_measurement"],
            "dry_run": dry_run,
            **_unit_note(stored),
            "rows": {
                "columns": ["start", "old_state", "old_sum", "new_state", "new_sum"],
                "rows": [
                    [
                        dt_util.as_local(row["start"]).isoformat(),
                        _rounded(old.get(key(row), {}).get("state")),
                        _rounded(old.get(key(row), {}).get("sum")),
                        _rounded(row.get("state")),
                        _rounded(row.get("sum")),
                    ]
                    for row in rows
                ],
            },
        }
        # The hours after the import keep their sum. If the last imported sum
        # differs from the one that hour had, the next hour makes a jump.
        was, following = old.get(key(rows[-1])), old.get(key(rows[-1]) + 3600_000)
        if "sum" in rows[-1] and following and following.get("sum") is not None:
            reference = was["sum"] if was and was.get("sum") is not None else following["sum"]
            difference = round(rows[-1]["sum"] - reference, 3)
            if difference and (was or difference > 0):
                after = _local(following["start"])
                result["warning"] = {
                    "difference": difference,
                    "message": (
                        f"the last imported sum does not connect to the existing "
                        f"sum of the next hour ({after}): it is off by {difference} "
                        f"{metadata['unit_of_measurement'] or ''}".rstrip()
                        + f". Correct the later hours with adjust_statistic_sum, "
                        f"start_time {after}, adjustment {difference}"
                    ),
                }
        if dry_run:
            return result

        await send(
            "recorder/import_statistics",
            metadata=metadata,
            stats=[{**row, "start": row["start"].isoformat()} for row in rows],
        )
        wanted = {key(r): r for r in rows}

        def written(found: list[dict]) -> bool:
            now = {r["start"]: r for r in found}
            return all(
                k in now
                and all(
                    now[k].get(f) is not None and round(now[k][f], 3) == round(r[f], 3)
                    for f in ("state", "sum")
                    if f in r
                )
                for k, r in wanted.items()
            )

        _, done = await _settled(read, written)
        return {**result, "imported": len(rows), "written": done}
