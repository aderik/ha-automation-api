"""Search and download HACS repositories.

HACS has no public API. This mirrors what its own websocket commands
(`hacs/repositories/list`, `hacs/repositories/add`, `hacs/repository/download`)
do with the HacsBase object in hass.data["hacs"], as of HACS 2.0.5.

Downloading runs third-party code with full Home Assistant rights, so it is
off until the "Allow HACS downloads" option of this integration is enabled.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_ALLOW_HACS, DOMAIN


# sort name -> (key, descending)
_SORTS = {
    "stars": (lambda r: r.data.stargazers_count or 0, True),
    "last_updated": (lambda r: r.data.last_updated or "", True),
    "name": (lambda r: (r.display_name or "").lower(), False),
}


# Removing these through the API would cut off HACS or this API itself.
_PROTECTED = {"hacs/integration", "aderik/ha-automation-api"}


class HacsError(Exception):
    """A request HACS can't fulfil; reported to the caller as a 400."""


def downloads_allowed(hass: HomeAssistant) -> bool:
    entry = hass.data.get(DOMAIN, {}).get("entry")
    return bool(entry and entry.options.get(CONF_ALLOW_HACS))


def _hacs(hass: HomeAssistant):
    hacs = hass.data.get("hacs")
    if hacs is None:
        raise HacsError("HACS is not installed")
    if hacs.system.disabled:
        raise HacsError(f"HACS is disabled: {hacs.system.disabled_reason}")
    return hacs


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
        and not repo.ignored_by_country_configuration
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

    A repository that isn't in the HACS store is added as a custom
    repository first, which needs `category`.
    """
    from custom_components.hacs.enums import HacsDispatchEvent

    hacs = _hacs_for_changes(hass)
    repository = _normalize(repository)
    repo = _find(hacs, repository)

    if repo is None:
        if not category:
            raise HacsError(
                f"{repository} is not in the HACS store; pass `category` "
                f"(one of {sorted(hacs.common.categories)}) to add it as a "
                "custom repository"
            )
        if category not in hacs.common.categories:
            raise HacsError(
                f"invalid category {category!r}, "
                f"expected one of {sorted(hacs.common.categories)}"
            )
        hacs.common.skip.discard(repository)
        await hacs.async_register_repository(
            repository_full_name=repository, category=category
        )
        repo = hacs.repositories.get_by_full_name(repository)
        if repo is None:
            raise HacsError(
                f"HACS could not add {repository}; validation failed, see the "
                "HACS log"
            )

    was_installed = repo.data.installed
    await repo.async_download_repository(ref=version)
    if not was_installed:
        hacs.async_dispatch(HacsDispatchEvent.RELOAD, {"force": True})
        await hacs.async_recreate_entities()
    await hacs.data.async_write()

    result = _repo_to_dict(repo)
    result["restart_required"] = repo.data.category == "integration"
    return result


async def remove(hass: HomeAssistant, repository: str) -> dict:
    """Uninstall a downloaded repository by id or `owner/name`, as HACS's
    own "Remove" does. Config entries of an integration are left alone."""
    hacs = _hacs_for_changes(hass)
    repository = _normalize(repository)
    repo = _find(hacs, repository)
    if repo is None or not repo.data.installed:
        raise HacsError(f"{repository} is not installed through HACS")
    if repo.data.full_name in _PROTECTED:
        raise HacsError(
            f"refusing to remove {repo.data.full_name}; remove it in the HACS UI"
        )

    repo.data.new = False
    try:
        await repo.update_repository(ignore_issues=True, force=True)
    except Exception:  # noqa: BLE001 - HACS's own remove ignores this too
        pass
    await repo.uninstall()
    await hacs.data.async_write()

    result = _repo_to_dict(repo)
    result["restart_required"] = repo.data.category == "integration"
    return result
