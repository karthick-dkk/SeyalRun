"""Which executors an admin has switched off, and the integration config they set.

Owned by identity-service (one row in za_settings) and read here, because the
decision is platform-wide: a Salt executor disabled on the Integration page must
be refused by an ad-hoc run, a scheduled run and a Zabbix-triggered run alike,
and those are three different call paths in this service.

Cached for a short TTL. The trade is deliberate: a disable takes effect within
_TTL seconds rather than instantly, in exchange for not adding an identity-service
round trip to the hot path of every job dispatch. A *failed* lookup does NOT fall
back to "nothing is disabled" silently forever — it keeps the last good answer and
retries on the next call, so a brief identity-service blip cannot quietly
re-enable an executor an admin turned off.
"""

from __future__ import annotations

import logging
import time

import httpx

from libs.servicetoken import mint

logger = logging.getLogger(__name__)

_TTL = 30.0
_cache: dict = {"at": 0.0, "value": None}


async def _fetch() -> dict | None:
    # Imported here, not at module scope: the cache policy below is the part
    # worth testing in isolation, and a module-level relative import would make
    # this file unloadable outside the running service.
    from .config import get_settings

    settings = get_settings()
    try:
        token = mint("automation-service", "identity-service", settings.service_jwt_secret)
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(
                f"{settings.identity_service_url}/api/v1/internal/integration-state",
                headers={"X-Service-Token": token},
            )
        if r.status_code != 200:
            return None
        return r.json() or {}
    except httpx.HTTPError as exc:
        logger.warning("integration state fetch failed: %s", exc)
        return None


async def get_state() -> dict:
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < _TTL:
        return _cache["value"]
    fresh = await _fetch()
    if fresh is not None:
        _cache["at"] = now
        _cache["value"] = fresh
    elif _cache["value"] is None:
        # Never successfully read: treat as "nothing configured, nothing disabled".
        # This is the fresh-deployment state, and it is the only case where an
        # unreachable identity-service means an executor is allowed to run.
        return {"disabled": {}, "config": {}}
    return _cache["value"]


async def executor_disabled(name: str) -> bool:
    state = await get_state()
    return name in ((state.get("disabled") or {}).get("executors") or [])


async def config_for(kind: str) -> dict:
    state = await get_state()
    return dict(((state.get("config") or {}).get(kind)) or {})


def _reset_cache_for_tests() -> None:
    _cache["at"] = 0.0
    _cache["value"] = None
