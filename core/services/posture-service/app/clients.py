"""Service-to-service calls: inventory (hosts), identity (visibility), automation
(the facts job). Each call mints a fresh ~60 s service token, like every other
service here; nothing is cached across requests except the shared HTTP pool.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException, status
from libs.servicetoken import mint

from .config import get_settings
from .deps import Caller

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _token(audience: str, subject: str | None = None) -> dict:
    return {"X-Service-Token": mint("posture-service", audience, get_settings().service_jwt_secret,
                                    subject=subject)}


def _upstream_error(name: str, exc: Exception) -> HTTPException:
    logger.error("%s request failed: %s", name, type(exc).__name__)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{name} unreachable")


def os_family(os_type: str | None) -> str:
    return "windows" if (os_type or "").lower().startswith("win") else "linux"


async def inventory_hosts() -> list[dict]:
    s = get_settings()
    try:
        r = await client().get(f"{s.inventory_service_url}/api/v1/hosts", headers=_token("inventory-service"))
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        raise _upstream_error("inventory-service", exc) from exc


async def inventory_host(host_id: str) -> dict | None:
    s = get_settings()
    try:
        r = await client().get(f"{s.inventory_service_url}/api/v1/internal/hosts/{host_id}",
                               headers=_token("inventory-service"))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as exc:
        raise _upstream_error("inventory-service", exc) from exc


async def visible_host_ids(caller: Caller) -> set[str] | None:
    """None = every host. Otherwise the ids this caller may see — the same
    resolution api-gateway's PAM host filter uses (identity-service authz,
    host-group grants already expanded to host ids)."""
    if caller.kiosk_host_id:
        return {caller.kiosk_host_id}
    if caller.sees_all_hosts:
        return None
    s = get_settings()
    try:
        r = await client().get(f"{s.identity_service_url}/api/v1/internal/authz/host-ids",
                               params={"user_id": caller.user_id}, headers=_token("identity-service"))
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as exc:
        raise _upstream_error("identity-service", exc) from exc
    if data.get("is_admin"):
        return None
    return set(data.get("host_ids") or [])


async def visible_hosts(caller: Caller) -> list[dict]:
    hosts = await inventory_hosts()
    allowed = await visible_host_ids(caller)
    if allowed is None:
        return hosts
    return [h for h in hosts if h.get("id") in allowed]


async def request_facts_runs(host_ids: list[str], requested_by: str) -> list[dict]:
    """Ask automation-service for one read-only facts run per host. Returns
    [{host_id, run_id, status, reused}]."""
    s = get_settings()
    try:
        r = await client().post(
            f"{s.automation_service_url}/api/v1/internal/posture/facts-runs",
            json={"host_ids": host_ids, "requested_by": requested_by},
            headers=_token("automation-service", subject=requested_by),
            timeout=60.0,
        )
        r.raise_for_status()
        return r.json().get("runs") or []
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail") or ""
        except ValueError:
            pass
        logger.error("automation-service refused facts runs: %s %s", exc.response.status_code, detail)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"automation-service refused the facts run: {detail or exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise _upstream_error("automation-service", exc) from exc


async def job_run(run_id: str) -> dict | None:
    s = get_settings()
    r = await client().get(f"{s.automation_service_url}/api/v1/internal/job-runs/{run_id}",
                           headers=_token("automation-service"), timeout=15.0)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


# ── integration state (which scanners an admin switched off) ─────────────────
# Same policy as automation-service's executor switch: cached 30 s; a failed
# lookup keeps the last good answer; never read at all = nothing disabled (a
# fresh deployment).
_state_cache: dict = {"at": 0.0, "value": None}
_STATE_TTL = 30.0


async def disabled_scanners() -> set[str]:
    import time

    now = time.monotonic()
    cached = _state_cache["value"]
    if cached is None or now - _state_cache["at"] >= _STATE_TTL:
        s = get_settings()
        try:
            r = await client().get(f"{s.identity_service_url}/api/v1/internal/integration-state",
                                   headers=_token("identity-service"), timeout=4.0)
            if r.status_code == 200:
                _state_cache["value"] = r.json() or {}
                _state_cache["at"] = now
        except httpx.HTTPError as exc:
            logger.warning("integration state fetch failed: %s", type(exc).__name__)
        cached = _state_cache["value"]
    if cached is None:
        return set()
    return set(((cached.get("disabled") or {}).get("scanners")) or [])
