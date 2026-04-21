"""Lovelace dashboard management.

Talks directly to the running Lovelace integration inside HA. Works with
storage‑mode dashboards (the default). YAML‑mode dashboards are surfaced
as read‑only and writes are refused.

Key concepts:
- The default dashboard (``/lovelace``) has ``url_path == None``.
- A custom dashboard (``/lovelace-moestuin``) has ``url_path == "moestuin"``.
- A dashboard's *config* is a dict like ``{"title": "...", "views": [...]}``.
  Each view has ``{"title": "...", "path": "...", "cards": [...]}``.

The Lovelace integration fires ``lovelace_updated`` after ``async_save``, so
the frontend refreshes automatically; users may need to reload the tab.
"""

from __future__ import annotations

import uuid
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store


STORAGE_KEY_DASHBOARDS = "lovelace_dashboards"
STORAGE_VERSION = 1


def _config_storage_key(key: str | None) -> str:
    """Map url_path key to HA storage key name."""
    return "lovelace" if key is None else f"lovelace.{key}"


async def _load_dashboards_storage(hass: HomeAssistant):
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY_DASHBOARDS)
    data = await store.async_load() or {"items": []}
    data.setdefault("items", [])
    return store, data


DEFAULT_SENTINELS = (None, "", "default")


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


def _dashboards_collection(hass: HomeAssistant):
    """Return the live DashboardsCollection if it exists in hass.data.

    Older HA versions exposed it under ``hass.data['lovelace']['dashboards_collection']``
    (or the equivalent dataclass attribute); newer versions keep it private.
    Callers should fall back to direct storage mutation when this returns None.
    """
    data = _lovelace_data(hass)
    return _attr(data, "dashboards_collection")


# --- Dashboards: list / create / delete / update ------------------------

async def list_dashboards(hass: HomeAssistant) -> list[dict]:
    """Return a summary of every Lovelace dashboard in this HA."""
    dashboards = _dashboards(hass)
    # Pull metadata (titles/icons) from the collection if reachable, else from storage.
    meta_by_url_path: dict[str | None, dict] = {}
    coll = _dashboards_collection(hass)
    if coll is not None:
        try:
            for item in coll.async_items():
                meta_by_url_path[item.get("url_path")] = item
        except Exception:
            pass
    if not meta_by_url_path:
        try:
            _, data = await _load_dashboards_storage(hass)
            for item in data.get("items", []):
                meta_by_url_path[item.get("url_path")] = item
        except Exception:
            pass

    out: list[dict] = []
    for url_path, cfg in dashboards.items():
        mode = _attr(cfg, "mode", "storage")
        meta = meta_by_url_path.get(url_path) or {}
        title = meta.get("title")
        if title is None and url_path is None:
            title = "Overview"
        out.append(
            {
                "url_path": url_path if url_path is not None else "default",
                "mode": mode,
                "title": title,
                "icon": meta.get("icon"),
                "show_in_sidebar": meta.get("show_in_sidebar"),
                "require_admin": meta.get("require_admin"),
            }
        )
    return out


async def create_dashboard(
    hass: HomeAssistant,
    url_path: str,
    title: str,
    icon: str | None = None,
    show_in_sidebar: bool = True,
    require_admin: bool = False,
) -> dict:
    """Create a new storage‑mode dashboard.

    Uses the live ``DashboardsCollection`` when available; otherwise writes
    directly to ``.storage/lovelace_dashboards`` and flags ``restart_required``.
    """
    if not url_path or url_path in DEFAULT_SENTINELS:
        raise ValueError("url_path must be a non-empty slug (not 'default')")

    item = {
        "url_path": url_path,
        "title": title,
        "mode": "storage",
        "show_in_sidebar": show_in_sidebar,
        "require_admin": require_admin,
    }
    if icon:
        item["icon"] = icon

    coll = _dashboards_collection(hass)
    if coll is not None:
        created = await coll.async_create_item(item)
        return {**created, "restart_required": False}

    # Fallback: write straight into .storage/lovelace_dashboards.
    store, data = await _load_dashboards_storage(hass)
    if any(i.get("url_path") == url_path for i in data["items"]):
        raise ValueError(f"dashboard '{url_path}' already exists")
    item["id"] = uuid.uuid4().hex
    data["items"].append(item)
    await store.async_save(data)
    return {**item, "restart_required": True}


async def update_dashboard(
    hass: HomeAssistant,
    url_path: str,
    changes: dict,
) -> dict:
    """Update metadata of a dashboard (title, icon, show_in_sidebar...)."""
    key = _resolve_key(url_path)
    if key is None:
        raise ValueError("cannot update metadata of the default dashboard")

    coll = _dashboards_collection(hass)
    if coll is not None:
        item_id = None
        for item in coll.async_items():
            if item.get("url_path") == key:
                item_id = item.get("id")
                break
        if item_id is None:
            raise ValueError(f"dashboard '{url_path}' not found")
        updated = await coll.async_update_item(item_id, changes)
        return {**updated, "restart_required": False}

    # Fallback
    store, data = await _load_dashboards_storage(hass)
    for item in data["items"]:
        if item.get("url_path") == key:
            item.update(changes)
            await store.async_save(data)
            return {**item, "restart_required": True}
    raise ValueError(f"dashboard '{url_path}' not found")


async def delete_dashboard(hass: HomeAssistant, url_path: str) -> bool:
    """Delete a custom dashboard. The default dashboard cannot be deleted."""
    key = _resolve_key(url_path)
    if key is None:
        raise ValueError("cannot delete the default dashboard")

    coll = _dashboards_collection(hass)
    if coll is not None:
        item_id = None
        for item in coll.async_items():
            if item.get("url_path") == key:
                item_id = item.get("id")
                break
        if item_id is None:
            return False
        await coll.async_delete_item(item_id)
        return True

    # Fallback
    store, data = await _load_dashboards_storage(hass)
    new_items = [i for i in data["items"] if i.get("url_path") != key]
    if len(new_items) == len(data["items"]):
        return False
    data["items"] = new_items
    await store.async_save(data)
    # Also wipe config storage for this dashboard.
    try:
        cfg_store = Store(hass, STORAGE_VERSION, _config_storage_key(key))
        await cfg_store.async_remove()
    except Exception:
        pass
    return True


# --- Dashboard config ----------------------------------------------------

async def get_config(hass: HomeAssistant, url_path: str | None) -> dict | None:
    """Return the full config of a dashboard (``{"views": [...], ...}``)."""
    key = _resolve_key(url_path)
    dashboards = _dashboards(hass)
    cfg = dashboards.get(key)
    if cfg is not None:
        try:
            return await cfg.async_load(force=True)
        except Exception:
            return {"views": []}
    # Fallback: read directly from .storage/lovelace(.<key>)
    store = Store(hass, STORAGE_VERSION, _config_storage_key(key))
    data = await store.async_load()
    if data is None:
        # Dashboard may exist in lovelace_dashboards but has no config yet.
        _, ddata = await _load_dashboards_storage(hass)
        if key is not None and any(
            i.get("url_path") == key for i in ddata.get("items", [])
        ):
            return {"views": []}
        return None
    # HA wraps the raw config under 'config' when stored via LovelaceStorage.
    return data.get("config") if isinstance(data, dict) and "config" in data else data


async def set_config(
    hass: HomeAssistant, url_path: str | None, config: dict
) -> None:
    """Overwrite the full config of a dashboard.

    If the dashboard's ``LovelaceStorage`` is live we use its ``async_save``
    (frontend gets a ``lovelace_updated`` event immediately). Otherwise we
    write directly to ``.storage/lovelace(.<url_path>)``; changes are picked
    up on the next HA restart.
    """
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    if "views" not in config or not isinstance(config["views"], list):
        raise ValueError("config must contain a 'views' list")

    key = _resolve_key(url_path)
    dashboards = _dashboards(hass)
    cfg = dashboards.get(key)
    if cfg is not None:
        mode = _attr(cfg, "mode", "storage")
        if mode != "storage":
            raise ValueError(
                f"dashboard '{url_path}' is in '{mode}' mode and cannot be written"
            )
        await cfg.async_save(config)
        return
    # Fallback: write directly to storage file.
    store = Store(hass, STORAGE_VERSION, _config_storage_key(key))
    await store.async_save({"config": config})


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
