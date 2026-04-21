"""Managed configuration package file.

Everything the Automation API manages outside of automations.yaml lives in a
single HA "package" file at ``<config>/packages/automation_api.yaml``. The
user must enable packages once::

    homeassistant:
      packages: !include_dir_named packages

After that, helpers, template sensors, history_stats sensors, and notify
groups can be created/updated/deleted via the REST API. Reload services are
invoked automatically where they exist.
"""

from __future__ import annotations

import os
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util.file import write_utf8_file_atomic
from homeassistant.util.yaml import dump, load_yaml


PACKAGE_DIR = "packages"
PACKAGE_FILE = "automation_api.yaml"


HELPER_DOMAINS: set[str] = {
    "input_boolean",
    "input_datetime",
    "input_number",
    "input_select",
    "input_text",
    "input_button",
}

# Domains whose YAML config can be reloaded at runtime via <domain>.reload.
RELOADABLE_DOMAINS: set[str] = HELPER_DOMAINS | {
    "template",
    "automation",
    "script",
    "scene",
}

# Template entity types we expose.
TEMPLATE_TYPES: set[str] = {"sensor", "binary_sensor", "switch", "button", "number", "select"}


def _package_path(hass: HomeAssistant) -> str:
    return hass.config.path(PACKAGE_DIR, PACKAGE_FILE)


# --- File IO (sync, called via executor) ---------------------------------

def _read_sync(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    data = load_yaml(path) or {}
    return data if isinstance(data, dict) else {}


def _write_sync(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_utf8_file_atomic(path, dump(data))


async def read_package(hass: HomeAssistant) -> dict:
    return await hass.async_add_executor_job(_read_sync, _package_path(hass))


async def write_package(hass: HomeAssistant, data: dict) -> None:
    await hass.async_add_executor_job(_write_sync, _package_path(hass), data)


async def overwrite_package(hass: HomeAssistant, data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("package content must be a dict")
    await write_package(hass, data)


# --- Helpers (dict-keyed sections) ---------------------------------------

async def upsert_helper(
    hass: HomeAssistant, domain: str, helper_id: str, config: dict
) -> None:
    if domain not in HELPER_DOMAINS:
        raise ValueError(f"unsupported helper domain: {domain}")
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    pkg = await read_package(hass)
    section = pkg.get(domain) or {}
    if not isinstance(section, dict):
        raise ValueError(f"section {domain!r} is not a dict")
    section[helper_id] = config
    pkg[domain] = section
    await write_package(hass, pkg)


async def delete_helper(hass: HomeAssistant, domain: str, helper_id: str) -> bool:
    pkg = await read_package(hass)
    section = pkg.get(domain)
    if not isinstance(section, dict) or helper_id not in section:
        return False
    del section[helper_id]
    if section:
        pkg[domain] = section
    else:
        del pkg[domain]
    await write_package(hass, pkg)
    return True


async def get_helper(
    hass: HomeAssistant, domain: str, helper_id: str
) -> dict | None:
    pkg = await read_package(hass)
    section = pkg.get(domain) or {}
    if not isinstance(section, dict):
        return None
    return section.get(helper_id)


async def list_helpers(hass: HomeAssistant, domain: str) -> dict:
    pkg = await read_package(hass)
    section = pkg.get(domain) or {}
    return section if isinstance(section, dict) else {}


# --- Template sensors/binary_sensors -------------------------------------

def _find_template_group(lst: list, ttype: str) -> dict | None:
    for g in lst:
        if isinstance(g, dict) and isinstance(g.get(ttype), list):
            return g
    return None


async def upsert_template(
    hass: HomeAssistant, ttype: str, name: str, config: dict
) -> None:
    if ttype not in TEMPLATE_TYPES:
        raise ValueError(f"template type must be one of {sorted(TEMPLATE_TYPES)}")
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    pkg = await read_package(hass)
    lst = pkg.get("template") or []
    if not isinstance(lst, list):
        raise ValueError("template section is not a list")
    group = _find_template_group(lst, ttype)
    if group is None:
        group = {ttype: []}
        lst.append(group)
    items: list = group[ttype]
    item = {"name": name, **{k: v for k, v in config.items() if k != "name"}}
    for i, existing in enumerate(items):
        if isinstance(existing, dict) and existing.get("name") == name:
            items[i] = item
            break
    else:
        items.append(item)
    pkg["template"] = lst
    await write_package(hass, pkg)


async def delete_template(
    hass: HomeAssistant, ttype: str, name: str
) -> bool:
    pkg = await read_package(hass)
    lst = pkg.get("template")
    if not isinstance(lst, list):
        return False
    changed = False
    new_lst: list = []
    for g in lst:
        if isinstance(g, dict) and isinstance(g.get(ttype), list):
            filtered = [
                it for it in g[ttype]
                if not (isinstance(it, dict) and it.get("name") == name)
            ]
            if len(filtered) != len(g[ttype]):
                changed = True
            rest = {k: v for k, v in g.items() if k != ttype}
            if filtered:
                rest[ttype] = filtered
            if rest:
                new_lst.append(rest)
        else:
            new_lst.append(g)
    if not changed:
        return False
    if new_lst:
        pkg["template"] = new_lst
    else:
        del pkg["template"]
    await write_package(hass, pkg)
    return True


async def get_template(
    hass: HomeAssistant, ttype: str, name: str
) -> dict | None:
    pkg = await read_package(hass)
    lst = pkg.get("template") or []
    if not isinstance(lst, list):
        return None
    for g in lst:
        if isinstance(g, dict) and isinstance(g.get(ttype), list):
            for item in g[ttype]:
                if isinstance(item, dict) and item.get("name") == name:
                    return item
    return None


async def list_templates(hass: HomeAssistant, ttype: str) -> list[dict]:
    pkg = await read_package(hass)
    lst = pkg.get("template") or []
    if not isinstance(lst, list):
        return []
    out: list[dict] = []
    for g in lst:
        if isinstance(g, dict) and isinstance(g.get(ttype), list):
            out.extend(g[ttype])
    return out


# --- history_stats (sensor platform entry) -------------------------------

async def upsert_history_stats(
    hass: HomeAssistant, name: str, config: dict
) -> None:
    if not isinstance(config, dict):
        raise ValueError("config must be a dict")
    pkg = await read_package(hass)
    lst = pkg.get("sensor") or []
    if not isinstance(lst, list):
        raise ValueError("sensor section is not a list")
    item: dict[str, Any] = {"platform": "history_stats", "name": name}
    for k, v in config.items():
        if k in ("platform", "name"):
            continue
        item[k] = v
    for i, existing in enumerate(lst):
        if (
            isinstance(existing, dict)
            and existing.get("platform") == "history_stats"
            and existing.get("name") == name
        ):
            lst[i] = item
            break
    else:
        lst.append(item)
    pkg["sensor"] = lst
    await write_package(hass, pkg)


async def delete_history_stats(hass: HomeAssistant, name: str) -> bool:
    pkg = await read_package(hass)
    lst = pkg.get("sensor")
    if not isinstance(lst, list):
        return False
    new = [
        it for it in lst
        if not (
            isinstance(it, dict)
            and it.get("platform") == "history_stats"
            and it.get("name") == name
        )
    ]
    if len(new) == len(lst):
        return False
    if new:
        pkg["sensor"] = new
    else:
        del pkg["sensor"]
    await write_package(hass, pkg)
    return True


async def get_history_stats(hass: HomeAssistant, name: str) -> dict | None:
    pkg = await read_package(hass)
    lst = pkg.get("sensor") or []
    if not isinstance(lst, list):
        return None
    for it in lst:
        if (
            isinstance(it, dict)
            and it.get("platform") == "history_stats"
            and it.get("name") == name
        ):
            return it
    return None


async def list_history_stats(hass: HomeAssistant) -> list[dict]:
    pkg = await read_package(hass)
    lst = pkg.get("sensor") or []
    if not isinstance(lst, list):
        return []
    return [
        it for it in lst
        if isinstance(it, dict) and it.get("platform") == "history_stats"
    ]


# --- Notify groups -------------------------------------------------------

def _normalize_services(services: Any) -> list[dict]:
    if not isinstance(services, list):
        raise ValueError("services must be a list")
    normalized: list[dict] = []
    for s in services:
        if isinstance(s, str):
            normalized.append({"service": s})
        elif isinstance(s, dict) and "service" in s:
            normalized.append(s)
        else:
            raise ValueError(f"invalid service entry: {s!r}")
    return normalized


async def upsert_notify_group(
    hass: HomeAssistant, name: str, services: list, extra: dict | None = None
) -> None:
    pkg = await read_package(hass)
    lst = pkg.get("notify") or []
    if not isinstance(lst, list):
        raise ValueError("notify section is not a list")
    item = {
        "platform": "group",
        "name": name,
        "services": _normalize_services(services),
    }
    if extra:
        for k, v in extra.items():
            if k not in ("platform", "name", "services"):
                item[k] = v
    for i, existing in enumerate(lst):
        if (
            isinstance(existing, dict)
            and existing.get("platform") == "group"
            and existing.get("name") == name
        ):
            lst[i] = item
            break
    else:
        lst.append(item)
    pkg["notify"] = lst
    await write_package(hass, pkg)


async def delete_notify_group(hass: HomeAssistant, name: str) -> bool:
    pkg = await read_package(hass)
    lst = pkg.get("notify")
    if not isinstance(lst, list):
        return False
    new = [
        it for it in lst
        if not (
            isinstance(it, dict)
            and it.get("platform") == "group"
            and it.get("name") == name
        )
    ]
    if len(new) == len(lst):
        return False
    if new:
        pkg["notify"] = new
    else:
        del pkg["notify"]
    await write_package(hass, pkg)
    return True


async def get_notify_group(hass: HomeAssistant, name: str) -> dict | None:
    pkg = await read_package(hass)
    lst = pkg.get("notify") or []
    if not isinstance(lst, list):
        return None
    for it in lst:
        if (
            isinstance(it, dict)
            and it.get("platform") == "group"
            and it.get("name") == name
        ):
            return it
    return None


async def list_notify_groups(hass: HomeAssistant) -> list[dict]:
    pkg = await read_package(hass)
    lst = pkg.get("notify") or []
    if not isinstance(lst, list):
        return []
    return [
        it for it in lst
        if isinstance(it, dict) and it.get("platform") == "group"
    ]


# --- Reload / restart ----------------------------------------------------

async def reload_domain(hass: HomeAssistant, domain: str) -> bool:
    if domain not in RELOADABLE_DOMAINS:
        return False
    try:
        await hass.services.async_call(domain, "reload", {}, blocking=True)
        return True
    except Exception:
        return False


async def reload_all(hass: HomeAssistant) -> bool:
    try:
        await hass.services.async_call(
            "homeassistant", "reload_all", {}, blocking=True
        )
        return True
    except Exception:
        return False


async def restart(hass: HomeAssistant) -> None:
    await hass.services.async_call(
        "homeassistant", "restart", {}, blocking=True
    )
