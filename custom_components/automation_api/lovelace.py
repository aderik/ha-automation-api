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

A dashboard can be changed as a whole, per view or card, or at any depth
with a patch; a change can carry the ``config_hash`` of the config it was
based on and is then refused when the dashboard changed since.

The resources of custom cards go through Lovelace's ``lovelace/resources/*``
commands in the same way.

Key concepts:
- The default dashboard has ``url_path == None`` (``default`` in this API).
- A custom dashboard at ``/my-garden`` has ``url_path == "my-garden"``.
- A dashboard's *config* is a dict like ``{"title": "...", "views": [...]}``.
  Each view has ``{"title": "...", "path": "...", "cards": [...]}``.

The Lovelace integration fires ``lovelace_updated`` after ``async_save``, so
the frontend refreshes automatically; users may need to reload the tab.
"""

from __future__ import annotations

import copy
import hashlib
import json
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
    data = hass.data.get("lovelace")
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


class StaleConfig(ValueError):
    """The dashboard changed since the config a change was based on."""

    def __init__(self, current_hash: str) -> None:
        super().__init__("the dashboard changed since it was read; read it again")
        self.current_hash = current_hash


def config_hash(config: Any) -> str:
    """Fingerprint of a dashboard config, to refuse a change based on a
    stale read."""
    text = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _check_fresh(hass: HomeAssistant, url_path: str | None, based_on: str | None) -> None:
    if based_on is None:
        return
    current = config_hash(await get_config(hass, url_path))
    if current != based_on:
        raise StaleConfig(current)


async def set_config(
    hass: HomeAssistant,
    url_path: str | None,
    config: dict,
    based_on: str | None = None,
) -> None:
    """Overwrite the full config of a dashboard through its live
    ``LovelaceStorage`` (the frontend gets ``lovelace_updated`` at once).

    `based_on` is the hash of the config the new one was made from; when the
    dashboard has another config by now, nothing is saved."""
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
    await _check_fresh(hass, url_path, based_on)
    await cfg.async_save(config)


# --- Patches: a change anywhere in a config -------------------------------

_MISSING = object()


def _tokens(path: Any, index: int) -> list[str]:
    """The steps of a JSON Pointer such as ``/views/0/sections/1/cards/-``."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"operation {index}: 'path' must start with '/'")
    return [t.replace("~1", "/").replace("~0", "~") for t in path.split("/")[1:]]


def _step(node: Any, token: str, path: str, index: int) -> Any:
    """The child of `node` that `token` names."""
    if isinstance(node, dict):
        if token in node:
            return node[token]
    elif isinstance(node, list):
        if token.isdigit() and int(token) < len(node):
            return node[int(token)]
    raise ValueError(f"operation {index}: there is nothing at '{path}'")


def apply_patch(config: dict, operations: Any) -> dict:
    """A copy of `config` with the operations of a JSON Patch applied.

    Operations are ``{"op", "path", "value"}`` with op ``add``, ``remove``,
    ``replace`` or ``test``; ``path`` is a JSON Pointer, in which ``-`` adds
    to the end of a list. They run in order, and one that fails leaves the
    config as it was.
    """
    if not isinstance(operations, list) or not operations:
        raise ValueError("'patch' must be a list of operations")
    result = copy.deepcopy(config)
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ValueError(f"operation {index}: must be an object")
        op, path = operation.get("op"), operation.get("path")
        if op not in ("add", "remove", "replace", "test"):
            raise ValueError(
                f"operation {index}: 'op' must be add, remove, replace or test"
            )
        if op != "remove" and "value" not in operation:
            raise ValueError(f"operation {index}: '{op}' needs a 'value'")
        *parents, last = _tokens(path, index)
        node = result
        for token in parents:
            node = _step(node, token, path, index)
        value = copy.deepcopy(operation.get("value"))
        if isinstance(node, list):
            if op == "add" and last == "-":
                node.append(value)
                continue
            if not last.isdigit() or int(last) > len(node) - (op != "add"):
                raise ValueError(f"operation {index}: there is nothing at '{path}'")
            at = int(last)
            if op == "add":
                node.insert(at, value)
            elif op == "remove":
                del node[at]
            elif op == "replace":
                node[at] = value
            elif node[at] != value:
                raise ValueError(f"operation {index}: the value at '{path}' is not as tested")
        elif isinstance(node, dict):
            if op == "add":
                node[last] = value
            elif last not in node:
                raise ValueError(f"operation {index}: there is nothing at '{path}'")
            elif op == "remove":
                del node[last]
            elif op == "replace":
                node[last] = value
            elif node[last] != value:
                raise ValueError(f"operation {index}: the value at '{path}' is not as tested")
        else:
            raise ValueError(f"operation {index}: there is nothing at '{path}'")
    return result


async def patch_config(
    hass: HomeAssistant,
    url_path: str | None,
    operations: Any,
    based_on: str | None = None,
) -> dict:
    """Apply a patch to the config of a dashboard and save it; returns
    whether anything changed and the hash of the config it has now."""
    config = await get_config(hass, url_path)
    if config is None:
        raise ValueError(f"dashboard '{url_path}' not found")
    if based_on is not None and config_hash(config) != based_on:
        raise StaleConfig(config_hash(config))
    patched = apply_patch(config, operations)
    changed = patched != config
    if changed:
        await set_config(hass, url_path, patched)
    return {"changed": changed, "config_hash": config_hash(patched)}


# --- Resources: the files of custom cards ---------------------------------

RESOURCE_TYPES = ("module", "js", "css", "html")


async def _resources_command(
    hass: HomeAssistant, token: str | None, command: str, **fields: Any
) -> Any:
    """Send one of Lovelace's `lovelace/resources/*` commands as the caller;
    None where Lovelace has no such resource."""
    async with _connect(hass, token, ValueError) as send:
        try:
            return await send(f"lovelace/resources/{command}", **fields)
        except ValueError as e:
            if str(e).endswith("(not_found)"):
                return None
            raise


def _resource_fields(url: Any, resource_type: Any) -> dict:
    fields = {}
    if url is not None:
        if not isinstance(url, str) or not url:
            raise ValueError("'url' must be a non-empty string")
        fields["url"] = url
    if resource_type is not None:
        if resource_type not in RESOURCE_TYPES:
            raise ValueError(f"'type' must be one of {list(RESOURCE_TYPES)}")
        fields["res_type"] = resource_type
    return fields


async def list_resources(hass: HomeAssistant, token: str | None) -> list[dict]:
    return await _resources_command(hass, token, "list") or []


async def create_resource(
    hass: HomeAssistant, token: str | None, url: Any, resource_type: Any = "module"
) -> dict:
    if url is None:
        raise ValueError("missing 'url'")
    return await _resources_command(
        hass, token, "create", **_resource_fields(url, resource_type or "module")
    )


async def update_resource(
    hass: HomeAssistant,
    token: str | None,
    resource_id: str,
    url: Any = None,
    resource_type: Any = None,
) -> dict | None:
    """Change the url or type of a resource; None if there is none with that id."""
    fields = _resource_fields(url, resource_type)
    if not fields:
        raise ValueError("give a 'url' or a 'type' to change")
    return await _resources_command(
        hass, token, "update", resource_id=resource_id, **fields
    )


async def delete_resource(
    hass: HomeAssistant, token: str | None, resource_id: str
) -> bool:
    known = {item["id"] for item in await list_resources(hass, token)}
    if resource_id not in known:
        return False
    await _resources_command(hass, token, "delete", resource_id=resource_id)
    return True


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
