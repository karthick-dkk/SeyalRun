"""Metrics endpoints — Zabbix HTTP Agent compatible JSON.

All endpoints:
  - Require PAT with scope `metrics:read` via `Authorization: Bearer sr_...`
  - Read from Redis cache (TTL 30s). Cache miss → collect on-demand then cache.
  - Respond with Content-Type: application/json; charset=utf-8
  - p95 target < 100ms (served from cache)

Zabbix LLD arrays use the `data` key per Zabbix convention.
All timestamps are Unix epoch seconds (integers).
All numeric values are numbers, not strings.
"""
from __future__ import annotations

import json
import time

import redis.asyncio as aioredis
from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import JSONResponse

from .. import obs
from ..collectors import collect_all, collect_app, collect_audit, collect_executions
from ..collectors import collect_housekeeping, collect_integrations, collect_playbooks
from ..collectors import collect_queue, collect_system, collect_webhooks
from ..config import get_settings
from ..security import AuthError, verify_pat

router = APIRouter(tags=["metrics"])

_settings = get_settings()
_redis = aioredis.from_url(_settings.redis_url, decode_responses=True)

_CACHE_KEY_PREFIX = "seyalrun:metrics:"
_DEFAULT_TTL = 30  # seconds


# ── Auth dependency ──────────────────────────────────────────────────────────

async def _require_metrics_token(authorization: str | None = Header(default=None, alias="Authorization")) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="metrics:read token required — use Authorization: Bearer sr_...",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    if not token.startswith("sr_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="use a Personal Access Token (sr_...) for metrics access, not a session JWT",
        )
    try:
        identity = await verify_pat(token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if "metrics:read" not in (identity.get("scopes") or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="token lacks metrics:read scope — regenerate with metrics:read",
        )
    return identity


async def _cached(key: str, collector, ttl: int = _DEFAULT_TTL) -> dict:
    """Read from Redis cache; on miss, call collector and store result."""
    raw = await _redis.get(_CACHE_KEY_PREFIX + key)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    data = await collector()
    try:
        await _redis.setex(_CACHE_KEY_PREFIX + key, ttl, json.dumps(data))
    except Exception:
        pass
    return data


def _resp(data: dict) -> JSONResponse:
    return JSONResponse(
        content=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/api/metrics/system", summary="Host/container resources")
async def metrics_system(_: dict = Header(default=None)) -> JSONResponse:
    """CPU, memory, disk, uptime. No auth — these are container-level stats."""
    return _resp(await _cached("system", collect_system, ttl=15))


@router.get("/api/metrics/app", summary="Core application counters")
async def metrics_app(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_metrics_token(authorization)
    return _resp(await _cached("app", collect_app))


@router.get("/api/metrics/playbooks", summary="Playbook stats")
async def metrics_playbooks(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_metrics_token(authorization)
    return _resp(await _cached("playbooks", collect_playbooks))


@router.get("/api/metrics/webhooks", summary="Webhook integration stats (LLD)")
async def metrics_webhooks(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_metrics_token(authorization)
    return _resp(await _cached("webhooks", collect_webhooks))


@router.get("/api/metrics/executions", summary="Execution/job stats")
async def metrics_executions(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_metrics_token(authorization)
    return _resp(await _cached("executions", collect_executions))


@router.get("/api/metrics/housekeeping", summary="Housekeeping job status (LLD)")
async def metrics_housekeeping(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_metrics_token(authorization)
    return _resp(await _cached("housekeeping", collect_housekeeping, ttl=60))


@router.get("/api/metrics/integrations", summary="External integration health (LLD)")
async def metrics_integrations(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_metrics_token(authorization)
    return _resp(await _cached("integrations", collect_integrations, ttl=60))


@router.get("/api/metrics/queue", summary="Redis/Celery queue depths")
async def metrics_queue(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_metrics_token(authorization)
    return _resp(await _cached("queue", collect_queue, ttl=15))


@router.get("/api/metrics/audit", summary="Audit log stats")
async def metrics_audit(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_metrics_token(authorization)
    return _resp(await _cached("audit", collect_audit, ttl=60))


@router.get("/api/metrics/response-times", summary="Per-module response times (LLD)")
async def metrics_response_times(authorization: str | None = Header(default=None)) -> JSONResponse:
    """Rolling per-upstream-service latency (avg/p50/p95/max ms) measured at the gateway.
    The `data` array is Zabbix-LLD ready ({#SERVICE} macro per module)."""
    await _require_metrics_token(authorization)
    snap = obs.snapshot()
    data = [{"{#SERVICE}": svc, **stats} for svc, stats in snap.items()]
    return _resp({"data": data, "services": snap})


@router.get("/api/metrics/all", summary="Full aggregated bundle (all metrics in one call)")
async def metrics_all(authorization: str | None = Header(default=None)) -> JSONResponse:
    await _require_metrics_token(authorization)
    # Use cached bundle if available (60s TTL for the bundle itself)
    raw = await _redis.get(_CACHE_KEY_PREFIX + "all")
    if raw:
        try:
            return _resp(json.loads(raw))
        except json.JSONDecodeError:
            pass
    data = await collect_all()
    try:
        await _redis.setex(_CACHE_KEY_PREFIX + "all", 60, json.dumps(data))
    except Exception:
        pass
    return _resp(data)
