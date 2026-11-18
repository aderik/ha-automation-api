"""Search and download repositories through the Marketplace or HACS.

Home Assistant 2026.11 took HACS into core as the Marketplace integration;
older installs still have the HACS custom integration. Neither has a public
API, so this mirrors what their own websocket commands do with the manager
object: the `marketplace/repositories/*` and `marketplace/repository/*`
commands of the Marketplace (as of 2026.11), or `hacs/repositories/*` and
`hacs/repository/download` of HACS 2.0.5. The Marketplace wins when both
are loaded.

Downloading runs third-party code with full Home Assistant rights, so it is
off until the "Allow HACS downloads" option of this integration is enabled.
That option stands in for the Marketplace's own first-run warning.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import CONF_ALLOW_HACS, DOMAIN


# sort name -> (key, descending)
_SORTS = {
    "stars": (lambda r: r.data.stargazers_count or 0, True),
    "last_updated": (lambda r: r.data.last_updated or "", True),
    "name": (lambda r: (r.display_name or "").lower(), False),
}


# Removing these through the API would cut off HACS or this API itself.
_PROTECTED = {"hacs/integration", "aderik/ha-automation-api"}


MARKETPLACE = "marketplace"


class HacsError(Exception):
    """A request the store can't fulfil; reported to the caller as a 400."""


def downloads_allowed(hass: HomeAssistant) -> bool:
    entry = hass.data.get(DOMAIN, {}).get("entry")
    return bool(entry and entry.options.get(CONF_ALLOW_HACS))


def _is_marketplace(store) -> bool:
    return store.__class__.__name__ == "MarketplaceManager"


def _hacs(hass: HomeAssistant):
    """The loaded Marketplace, else the HACS custom integration."""
    if entries := hass.config_entries.async_loaded_entries(MARKETPLACE):
        store = entries[0].runtime_data
    elif entries := hass.config_entries.async_entries(MARKETPLACE):
        # set up late in stage 2, about a minute after a restart
        raise HacsError(f"the Marketplace is not loaded ({entries[0].state})")
    elif (store := hass.data.get("hacs")) is None:
        raise HacsError("neither the Marketplace nor HACS is loaded")
    if store.system.disabled:
        raise HacsError(f"store is disabled: {store.system.disabled_reason}")
    return store


def _hacs_for_changes(hass: HomeAssistant):
    """HACS, provided the user opted in to changes through this API."""
    if not downloads_allowed(hass):
        raise HacsError(
            "HACS downloads are disabled; enable 'Allow HACS downloads' in the "
            "Automation API integration options"
        )
    return _hacs(hass)


def _normalize(repository: str) -> str:
    return repository.removeprefix("https://github.com/").strip("/")


def _find(hacs, repository: str):
    return hacs.repositories.get_by_id(
        repository
    ) or hacs.repositories.get_by_full_name(repository)


def _repo_to_dict(repo) -> dict[str, Any]:
    return {
        "id": repo.data.id,
        "full_name": repo.data.full_name,
        "name": repo.display_name,
        "category": repo.data.category,
        "domain": repo.data.domain,
        "description": repo.data.description,
        "installed": repo.data.installed,
        "installed_version": repo.display_installed_version,
        "available_version": repo.display_available_version,
        "pending_upgrade": repo.pending_update,
        "stars": repo.data.stargazers_count,
        "downloads": repo.data.downloads,
        "last_updated": repo.data.last_updated,
    }


def _result(repo) -> dict[str, Any]:
    result = _repo_to_dict(repo)
    result["restart_required"] = repo.data.category == "integration"
    return result


async def search(
    hass: HomeAssistant,
    *,
    query: str | None = None,
    category: str | None = None,
    installed: bool | None = None,
    sort: str | None = None,
    limit: int = 25,
) -> list[dict]:
    if (sort or "stars") not in _SORTS:
        raise HacsError(f"invalid sort {sort!r}, expected one of {sorted(_SORTS)}")
    hacs = _hacs(hass)
    q = (query or "").lower()
    repos = [
        repo
        for repo in hacs.repositories.list_all
        if repo.data.last_fetched
        and not getattr(repo, "ignored_by_country_configuration", False)
        and (category is None or repo.data.category == category)
        and (installed is None or repo.data.installed == installed)
        and (
            not q
            or any(
                q in (value or "").lower()
                for value in (
                    repo.data.full_name,
                    repo.display_name,
                    repo.data.description,
                    repo.data.domain,
                )
            )
        )
    ]
    key, descending = _SORTS[sort or "stars"]
    repos.sort(key=key, reverse=descending)
    return [_repo_to_dict(r) for r in repos[:limit]]


async def download(
    hass: HomeAssistant,
    repository: str,
    *,
    category: str | None = None,
    version: str | None = None,
) -> dict:
    """Download (install or update) a repository by id or `owner/name`.

    A repository that isn't in the store is added as a custom repository
    first, which needs `category`.
    """
    store = _hacs_for_changes(hass)
    repository = _normalize(repository)
    try:
        repo = _find(store, repository) or await _add(store, repository, category)
        was_installed = repo.data.installed
        if _is_marketplace(store):
            await _marketplace_install(hass, repo, version, was_installed)
        else:
            await _hacs_install(store, repo, version, was_installed)
    except HomeAssistantError as e:  # the Marketplace's errors are translated
        raise HacsError(str(e)) from e
    return _result(repo)


async def _add(store, repository: str, category: str | None):
    if not category:
        raise HacsError(
            f"{repository} is not in the store; pass `category` "
            f"(one of {sorted(store.common.categories)}) to add it as a "
            "custom repository"
        )
    if category not in store.common.categories:
        raise HacsError(
            f"invalid category {category!r}, "
            f"expected one of {sorted(store.common.categories)}"
        )
    store.common.skip.discard(repository)
    # HACS returns None; the Marketplace returns its validation errors
    if errors := await store.async_register_repository(
        repository_full_name=repository, category=category
    ):
        raise HacsError(f"could not add {repository}: {errors[0]}")
    repo = store.repositories.get_by_full_name(
        store.common.renamed_repositories.get(repository, repository)
    )
    if repo is None:
        raise HacsError(
            f"could not add {repository}; validation failed, see the log"
        )
    return repo


async def _marketplace_install(hass, repo, version, was_installed) -> None:
    from homeassistant.components.marketplace.const import (
        SIGNAL_REPOSITORY_INSTALLED,
    )

    try:
        # writes the store's data itself
        await repo.async_install_repository(ref=version)
    finally:
        # as its websocket command: a first install gets its update entity
        if not was_installed and repo.data.installed:
            async_dispatcher_send(hass, SIGNAL_REPOSITORY_INSTALLED, repo)


async def _hacs_install(hacs, repo, version, was_installed) -> None:
    from custom_components.hacs.enums import HacsDispatchEvent

    await repo.async_download_repository(ref=version)
    if not was_installed:
        hacs.async_dispatch(HacsDispatchEvent.RELOAD, {"force": True})
        await hacs.async_recreate_entities()
    await hacs.data.async_write()


async def remove(hass: HomeAssistant, repository: str) -> dict:
    """Uninstall a downloaded repository by id or `owner/name`, as the
    store's own "Remove" does."""
    store = _hacs_for_changes(hass)
    repository = _normalize(repository)
    repo = _find(store, repository)
    if repo is None or not repo.data.installed:
        raise HacsError(f"{repository} is not installed through the store")
    if repo.data.full_name in _PROTECTED:
        raise HacsError(
            f"refusing to remove {repo.data.full_name}; remove it in the UI"
        )
    marketplace = _is_marketplace(store)
    # the Marketplace refuses while config entries still run the code;
    # HACS leaves them behind
    if (
        marketplace
        and repo.data.category == "integration"
        and repo.data.domain
        and hass.config_entries.async_entries(repo.data.domain)
    ):
        raise HacsError(
            f"{repo.data.full_name} is in use; remove the config entries of "
            f"{repo.data.domain!r} first"
        )

    repo.data.new = False
    try:
        await repo.update_repository(ignore_issues=True, force=True)
    except Exception:  # noqa: BLE001 - the store's own remove ignores this too
        pass
    try:
        await repo.uninstall()
    except HomeAssistantError as e:
        raise HacsError(str(e)) from e
    await store.data.async_write()
    return _result(repo)
