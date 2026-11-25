"""Service calls that can be asked about afterwards.

A service call that returns without an error only says that Home Assistant
handed the action to the integration; whether the device followed shows in
its state. Each call made here is an *operation*: it runs under its own
context and is remembered, so that its status can be read back: did the
entities it was aimed at change since, and which entities did it change.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any
import uuid

from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.util import dt as dt_util

from .const import DOMAIN

# ponytail: operations live in memory and the oldest make room; a status asked
# for after a restart or 500 calls later is "unknown". Store them if that hurts.
_KEPT = 500

# Longest a status request waits for entities to follow
MAX_WAIT = 60

_POLL = 0.25


@dataclass
class Operation:
    id: str
    domain: str
    service: str
    context: Context
    started: datetime
    targets: list[str]  # the entity ids the call named
    error: str | None = None


def _named_entities(data: dict) -> list[str]:
    """The entity ids a call names itself; areas, devices and labels are
    resolved by Home Assistant and show up through the context instead."""
    named = data.get("entity_id") or []
    if isinstance(named, str):
        named = named.split(",")
    return [entity_id.strip() for entity_id in named if isinstance(entity_id, str)]


def _operations(hass: HomeAssistant) -> dict[str, Operation]:
    return hass.data.setdefault(DOMAIN, {}).setdefault("operations", {})


async def call(
    hass: HomeAssistant, user_id: str | None, domain: str, service: str, data: dict | None
) -> tuple[Operation, Any]:
    """Call a service as an operation; returns it with the service's response.

    A call that fails does not raise: the operation carries the `error`.
    """
    data = data or {}
    operation = Operation(
        id=uuid.uuid4().hex,
        domain=domain,
        service=service,
        context=Context(user_id=user_id),
        started=dt_util.utcnow(),
        targets=_named_entities(data),
    )
    kept = _operations(hass)
    kept[operation.id] = operation
    while len(kept) > _KEPT:
        del kept[next(iter(kept))]
    if not hass.services.has_service(domain, service):
        operation.error = f"the service {domain}.{service} does not exist"
        return operation, None
    try:
        wants_response = (
            hass.services.supports_response(domain, service) is not SupportsResponse.NONE
        )
        response = await hass.services.async_call(
            domain,
            service,
            data,
            blocking=True,
            context=operation.context,
            return_response=wants_response,
        )
    except Exception as e:  # noqa: BLE001 - whatever the service raises is the outcome
        operation.error = f"{type(e).__name__}: {e}"
        return operation, None
    return operation, response


async def call_many(
    hass: HomeAssistant, user_id: str | None, calls: list, parallel: bool = True
) -> list[dict]:
    """Make several service calls; one that fails does not stop the others."""

    async def one(item: Any) -> dict:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("domain"), str)
            or not isinstance(item.get("service"), str)
            or not isinstance(item.get("data") or {}, dict)
        ):
            return {
                "status": "failed",
                "error": "an operation is {domain, service, data?}",
                "operation_id": None,
            }
        operation, response = await call(
            hass, user_id, item["domain"], item["service"], item.get("data")
        )
        result = {
            "domain": operation.domain,
            "service": operation.service,
            "operation_id": operation.id,
        }
        if operation.error:
            return {**result, "status": "failed", "error": operation.error}
        return {**result, "status": "ok", "response": response}

    if parallel:
        return list(await asyncio.gather(*(one(item) for item in calls)))
    return [await one(item) for item in calls]


def _look(hass: HomeAssistant, operation: Operation) -> dict:
    """What an operation has done so far."""
    # A state written because of this call carries its context
    by_context = {
        state.entity_id
        for state in hass.states.async_all()
        if state.context.id == operation.context.id
    }
    entities = []
    for entity_id in dict.fromkeys([*operation.targets, *sorted(by_context)]):
        state = hass.states.get(entity_id)
        entities.append(
            {
                "entity_id": entity_id,
                "state": None if state is None else state.state,
                # A device that answers late writes its state without the context
                "changed": state is not None
                and (entity_id in by_context or state.last_updated >= operation.started),
            }
        )
    if operation.error:
        status = "failed"
    elif all(entity["changed"] for entity in entities if entity["entity_id"] in operation.targets):
        status = "completed"
    else:
        status = "unconfirmed"
    return {
        "operation_id": operation.id,
        "domain": operation.domain,
        "service": operation.service,
        "status": status,
        "error": operation.error,
        "entities": entities,
    }


async def status(hass: HomeAssistant, operation_id: str, timeout: float = 0) -> dict:
    """The status of an operation, waiting up to `timeout` seconds for the
    entities it named to change.

    `completed`: the call returned and every entity it named changed since.
    `unconfirmed`: the call returned, but a named entity did not change (yet);
    it may have been in that state already. `failed`: the call raised.
    `unknown`: no such operation is remembered.
    """
    operation = _operations(hass).get(operation_id)
    if operation is None:
        return {"operation_id": operation_id, "status": "unknown"}
    waited = 0.0
    timeout = max(0.0, min(float(timeout), MAX_WAIT))
    while (found := _look(hass, operation))["status"] == "unconfirmed" and waited < timeout:
        await asyncio.sleep(_POLL)
        waited += _POLL
    return found
