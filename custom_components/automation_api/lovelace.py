"""Lovelace dashboard management.

Works with storage-mode dashboards (the default). YAML-mode dashboards are
surfaced as read-only and writes are refused.

Lovelace owns its dashboards and their storage; nothing here reads or writes
its files. The config of a dashboard (views and cards) is read and saved
through the live dashboard object Lovelace keeps for it. Creating, updating
and deleting a dashboard goes through Lovelace's own
``lovelace/dashboards/*`` WebSocket commands, sent as the caller (see
``ha_client``), so Lovelace validates the request and registers or removes
the panel itself, without a restart.

Key concepts:
- The default dashboard has ``url_path == None`` (``default`` in this API).
- A custom dashboard at ``/my-garden`` has ``url_path == "my-garden"``.
- A dashboard's *config* is a dict like ``{"title": "...", "views": [...]}``.
  Each view has ``{"title": "...", "path": "...", "cards": [...]}``.

The Lovelace integration fires ``lovelace_updated`` after ``async_save``, so
the frontend refreshes automatically; users may need to reload the tab.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .ha_client import connect as _connect


DEFAULT_SENTINELS = (None, "", "default")

# Fields of a websocket message, never those of a dashboard
_RESERVED = {"id", "type", "dashboard_id"}


def _resolve_key(url_path: str | None) -> str | None:
    """Map the HTTP path segment to Lovelace's dashboards dict key."""
    return None if url_path in DEFAULT_SENTINELS else url_path


def _lovelace_data(hass: HomeAssistant):
    """Return the lovelace integration's data object (dataclass or dict)."""
    try:
        from homeassistant.components.lovelace.const import DOMAIN
    except ImportError:
        raise ValueError("lovelace integration not available in this HA")
    data = hass.data.get(DOMAIN)
    if data is None:
        raise ValueError("lovelace integration is not set up")
    return data


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    """getattr/[] compatibility — Lovelace's data is a dataclass in newer HA,
    a plain dict in older HA."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _dashboards(hass: HomeAssistant) -> dict:
    data = _lovelace_data(hass)
    dash = _attr(data, "dashboards")
    if dash is None:
        raise ValueError("no dashboards registered")
    return dash


async def _dashboards_command(
    hass: HomeAssistant, token: str | None, command: str, **fields: Any
) -> Any:
    """Send one of Lovelace's `lovelace/dashboards/*` commands as the caller."""
    async with _connect(hass, token, ValueError) as send:
        return await send(f"lovelace/dashboards/{command}", **fields)


def _dashboard_id(dashboard: Any, url_path: str) -> str:
    """The id Lovelace's commands take for a storage dashboard."""
    item = _attr(dashboard, "config") or {}
    if not item.get("id"):
        raise ValueError(
            f"dashboard '{url_path}' is not a storage dashboard and cannot be changed"
        )
    return item["id"]


# --- Dashboards: list / create / delete / update ------------------------

async def list_dashboards(hass: HomeAssistant) -> list[dict]:
    """Return a summary of every Lovelace dashboard in this HA."""
    out: list[dict] = []
    for url_path, cfg in _dashboards(hass).items():
        # What the dashboard was created with; None for the default dashboard
        meta = _attr(cfg, "config") or {}
        title = meta.get("title")
        if title is None and url_path is None:
            title = "Overview"
        out.append(
            {
                "url_path": url_path if url_path is not None else "default",
                "mode": _attr(cfg, "mode", "storage"),
                "title": title,
                "icon": meta.get("icon"),
                "show_in_sidebar": meta.get("show_in_sidebar"),
                "require_admin": meta.get("require_admin"),
            }
        )
    return out


async def create_dashboard(
    hass: HomeAssistant,
    token: str | None,
    url_path: str,
    title: str,
    icon: str | None = None,
    show_in_sidebar: bool = True,
    require_admin: bool = False,
) -> dict:
    """Create a new storage-mode dashboard, as Settings → Dashboards does.

    Lovelace's rules apply: `url_path` needs a hyphen and may not be taken.
    """
    if not url_path or url_path in DEFAULT_SENTINELS:
        raise ValueError("url_path must be a non-empty slug (not 'default')")

    item = {
        "url_path": url_path,
        "title": title,
        "show_in_sidebar": show_in_sidebar,
        "require_admin": require_admin,
    }
    if icon:
        item["icon"] = icon
    return await _dashboards_command(hass, token, "create", **item)


async def update_dashboard(
    hass: HomeAssistant,
    token: str | None,
    url_path: str,
    changes: dict,
) -> dict:
    """Update metadata of a dashboard (title, icon, show_in_sidebar...)."""
    key = _resolve_key(url_path)
    if key is None:
        raise ValueError("cannot update metadata of the default dashboard")
    if reserved := _RESERVED & changes.keys():
        raise ValueError(f"cannot change {sorted(reserved)}")
    dashboard = _dashboards(hass).get(key)
    if dashboard is None:
        raise ValueError(f"dashboard '{url_path}' not found")
    return await _dashboards_command(
        hass,
        token,
        "update",
        dashboard_id=_dashboard_id(dashboard, url_path),
        **changes,
    )


async def delete_dashboard(
    hass: HomeAssistant, token: str | None, url_path: str
) -> bool:
    """Delete a custom dashboard. The default dashboard cannot be deleted."""
    key = _resolve_key(url_path)
    if key is None:
        raise ValueError("cannot delete the default dashboard")
    dashboard = _dashboards(hass).get(key)
    if dashboard is None:
        return False
    await _dashboards_command(
        hass, token, "delete", dashboard_id=_dashboard_id(dashboard, url_path)
    )
    return True


# --- Dashboard config ----------------------------------------------------

async def get_config(hass: HomeAssistant, url_path: str | None) -> dict | None:
    """Return the full config of a dashboard (``{"views": [...], ...}``),
    or None when Lovelace has no such dashboard."""
    cfg = _dashboards(hass).get(_resolve_key(url_path))
    if cfg is None:
        return None
    try:
        return await cfg.async_load(force=True)
    except Exception:
        return {"views": []}


async def set_config(
    hass: HomeAssistant, url_path: str | None, config: dict
) -> None:
    """Overwrite the full config of a dashboard through its live
    ``LovelaceStorage`` (the frontend gets ``lovelace_updated`` at once)."""
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    if "views" not in config or not isinstance(config["views"], list):
        raise ValueError("config must contain a 'views' list")

    cfg = _dashboards(hass).get(_resolve_key(url_path))
    if cfg is None:
        raise ValueError(f"dashboard '{url_path}' not found")
    mode = _attr(cfg, "mode", "storage")
    if mode != "storage":
        raise ValueError(
            f"dashboard '{url_path}' is in '{mode}' mode and cannot be written"
        )
    await cfg.async_save(config)


# --- Views (list mutations) ---------------------------------------------

async def _mutate(
    hass: HomeAssistant, url_path: str | None, mutator
) -> dict:
    """Load config, apply mutator(config), save it. Returns new config."""
    cfg = await get_config(hass, url_path)
    if cfg is None:
        raise ValueError(f"dashboard '{url_path}' not found")
    cfg.setdefault("views", [])
    result = mutator(cfg)
    await set_config(hass, url_path, cfg)
    return result if result is not None else cfg


async def append_view(
    hass: HomeAssistant, url_path: str | None, view: dict
) -> dict:
    if not isinstance(view, dict):
        raise ValueError("view must be a dict")

    def mut(cfg):
        cfg["views"].append(view)
        return {"view_index": len(cfg["views"]) - 1}

    return await _mutate(hass, url_path, mut)


async def replace_view(
    hass: HomeAssistant, url_path: str | None, view_index: int, view: dict
) -> dict:
    if not isinstance(view, dict):
        raise ValueError("view must be a dict")

    def mut(cfg):
        if view_index < 0 or view_index >= len(cfg["views"]):
            raise IndexError("view_index out of range")
        cfg["views"][view_index] = view
        return {"view_index": view_index}

    return await _mutate(hass, url_path, mut)


async def delete_view(
    hass: HomeAssistant, url_path: str | None, view_index: int
) -> dict:
    def mut(cfg):
        if view_index < 0 or view_index >= len(cfg["views"]):
            raise IndexError("view_index out of range")
        del cfg["views"][view_index]
        return {"view_index": view_index, "remaining_views": len(cfg["views"])}

    return await _mutate(hass, url_path, mut)


# --- Cards within a view ------------------------------------------------

def _view_cards(view: dict) -> list:
    cards = view.setdefault("cards", [])
    if not isinstance(cards, list):
        raise ValueError("view['cards'] is not a list")
    return cards


async def append_card(
    hass: HomeAssistant,
    url_path: str | None,
    view_index: int,
    card: dict,
) -> dict:
    if not isinstance(card, dict):
        raise ValueError("card must be a dict")

    def mut(cfg):
        if view_index < 0 or view_index >= len(cfg["views"]):
            raise IndexError("view_index out of range")
        cards = _view_cards(cfg["views"][view_index])
        cards.append(card)
        return {"view_index": view_index, "card_index": len(cards) - 1}

    return await _mutate(hass, url_path, mut)


async def replace_card(
    hass: HomeAssistant,
    url_path: str | None,
    view_index: int,
    card_index: int,
    card: dict,
) -> dict:
    if not isinstance(card, dict):
        raise ValueError("card must be a dict")

    def mut(cfg):
        if view_index < 0 or view_index >= len(cfg["views"]):
            raise IndexError("view_index out of range")
        cards = _view_cards(cfg["views"][view_index])
        if card_index < 0 or card_index >= len(cards):
            raise IndexError("card_index out of range")
        cards[card_index] = card
        return {"view_index": view_index, "card_index": card_index}

    return await _mutate(hass, url_path, mut)


async def delete_card(
    hass: HomeAssistant,
    url_path: str | None,
    view_index: int,
    card_index: int,
) -> dict:
    def mut(cfg):
        if view_index < 0 or view_index >= len(cfg["views"]):
            raise IndexError("view_index out of range")
        cards = _view_cards(cfg["views"][view_index])
        if card_index < 0 or card_index >= len(cards):
            raise IndexError("card_index out of range")
        del cards[card_index]
        return {
            "view_index": view_index,
            "card_index": card_index,
            "remaining_cards": len(cards),
        }

    return await _mutate(hass, url_path, mut)
