"""Search the Marketplace or HACS.

Home Assistant 2026.11 took HACS into core as the Marketplace integration;
older installs still have the HACS custom integration. This module only
reads: through `ha_client` it connects to this Home Assistant's WebSocket
API with the caller's access token and asks the store for its list of
repositories (`marketplace/repositories/list`, or `hacs/repositories/list`
of HACS 2.0.5). The Marketplace wins when both are set up.

Installing, adding and removing repositories is the store's job, with the
user in front of it. Every result carries the `url` of its page in the
store, where the user does that.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .ha_client import connect as _connect


# sort name -> (key, descending)
_SORTS = {
    "stars": (lambda r: r.get("stars") or 0, True),
    "last_updated": (lambda r: r.get("last_updated") or "", True),
    "name": (lambda r: (r.get("name") or "").lower(), False),
}

# config entry domain of a store -> the command that lists its repositories
# and the page of a repository in its panel; in order of preference
_STORES = {
    "marketplace": ("marketplace/repositories/list", "/marketplace/repository/{id}"),
    "hacs": ("hacs/repositories/list", "/hacs/repository/{id}"),
}

# What a caller gets of a repository, out of what the store lists
_FIELDS = (
    "id",
    "full_name",
    "name",
    "category",
    "domain",
    "description",
    "installed",
    "installed_version",
    "available_version",
    "pending_upgrade",
    "stars",
    "downloads",
    "last_updated",
)


class HacsError(Exception):
    """A request the store can't fulfil; reported to the caller as a 400."""


async def search(
    hass: HomeAssistant,
    token: str | None,
    *,
    query: str | None = None,
    category: str | None = None,
    installed: bool | None = None,
    sort: str | None = None,
    limit: int = 25,
) -> list[dict]:
    if (sort or "stars") not in _SORTS:
        raise HacsError(f"invalid sort {sort!r}, expected one of {sorted(_SORTS)}")
    store = next(
        (
            store
            for domain, store in _STORES.items()
            if hass.config_entries.async_entries(domain)
        ),
        None,
    )
    if store is None:
        raise HacsError("neither the Marketplace nor HACS is set up")
    command, page = store
    async with _connect(hass, token, HacsError) as send:
        # ponytail: the stores have no search command, so every search fetches
        # the whole list (a few MB); cache it for a minute if that gets slow
        rows = await send(command, **({"categories": [category]} if category else {}))
    q = (query or "").lower()
    rows = [
        row
        for row in rows
        if (installed is None or row["installed"] == installed)
        and (
            not q
            or any(
                q in (row.get(field) or "").lower()
                for field in ("full_name", "name", "description", "domain")
            )
        )
    ]
    key, descending = _SORTS[sort or "stars"]
    rows.sort(key=key, reverse=descending)
    return [
        {**{field: row.get(field) for field in _FIELDS}, "url": page.format(id=row["id"])}
        for row in rows[:limit]
    ]
