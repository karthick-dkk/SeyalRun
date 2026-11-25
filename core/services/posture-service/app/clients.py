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


async def request_facts_runs(host_ids: list[str], requested_by: str, approval: dict | None = None) -> list[dict]:
    """Ask automation-service for one read-only facts run per host. Returns
    [{host_id, run_id, status, reused}]."""
    s = get_settings()
    try:
        r = await client().post(
            f"{s.automation_service_url}/api/v1/internal/posture/facts-runs",
            json={"host_ids": host_ids, "requested_by": requested_by, "approval": approval},
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


# ── M3/M4: host operations, hooks, Zabbix, notifications ─────────────────────

class UpstreamError(RuntimeError):
    """A sibling service refused (``status``) or could not be reached (status 0)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status = status_code
        self.detail = detail


async def _call(method: str, base: str, audience: str, path: str, *, subject: str | None = None,
                json_body: dict | None = None, params: dict | None = None, timeout: float = 30.0) -> dict:
    try:
        r = await client().request(method, f"{base}{path}", json=json_body, params=params,
                                   headers=_token(audience, subject=subject), timeout=timeout)
    except httpx.HTTPError as exc:
        raise UpstreamError(0, f"{audience} unreachable ({type(exc).__name__})") from exc
    if r.status_code >= 400:
        try:
            detail = str(r.json().get("detail") or r.status_code)
        except ValueError:
            detail = str(r.status_code)
        raise UpstreamError(r.status_code, f"{audience}: {detail}")
    return r.json() if r.content else {}


async def host_op_run(*, action: str, op: str, args: dict, host_id: str, token: str, requested_by: str,
                      approval: dict | None, accountable: list[str] | None = None) -> dict:
    s = get_settings()
    return await _call("POST", s.automation_service_url, "automation-service", "/api/v1/internal/posture/host-runs",
                       subject=requested_by, json_body={
                           "action": action, "op": op, "args": args, "host_id": host_id, "token": token,
                           "requested_by": requested_by, "approval": approval,
                           "accountable_user_ids": accountable or []})


async def hook_template(template_id: str) -> dict:
    s = get_settings()
    return await _call("GET", s.automation_service_url, "automation-service",
                       f"/api/v1/internal/posture/hook-templates/{template_id}")


async def hook_run(*, template_id: str, expect_updated_at: str, host_id: str, requested_by: str, approval: dict | None) -> dict:
    s = get_settings()
    return await _call("POST", s.automation_service_url, "automation-service", "/api/v1/internal/posture/hook-runs",
                       subject=requested_by, json_body={
                           "job_template_id": template_id, "expect_updated_at": expect_updated_at,
                           "host_id": host_id, "requested_by": requested_by, "approval": approval})


async def notify(*, user_id: str | None, severity: str, title: str, message: str = "", source_id: str | None = None) -> None:
    """Best effort: a rollout must not stop because a notification could not be sent."""
    s = get_settings()
    try:
        await _call("POST", s.automation_service_url, "automation-service", "/api/v1/internal/notifications",
                    json_body={"user_id": user_id, "severity": severity, "title": title[:300],
                               "message": message[:2000], "source_type": "patch_rollout", "source_id": source_id})
    except UpstreamError as exc:
        logger.error("notification failed: %s", exc)


async def zabbix(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None) -> dict:
    s = get_settings()
    return await _call(method, s.zabbix_integration_service_url, "zabbix-integration-service",
                       f"/api/v1/internal/zabbix{path}", json_body=json_body, params=params)
