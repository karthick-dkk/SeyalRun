"""Health endpoints — Liveness and Readiness checks.

GET /api/health/live  — no auth, no DB, responds in <10ms, just proves the process is alive.
GET /api/health/ready — no auth, checks all downstream dependencies.
"""
from __future__ import annotations

import time

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import get_settings

router = APIRouter(tags=["health"])

_settings = get_settings()
_redis = aioredis.from_url(_settings.redis_url, decode_responses=True)

_START_TIME = time.time()


@router.get("/api/health/live", summary="Liveness")
async def liveness():
    """Instant liveness — proves the process responds. No DB, no Redis."""
    return {
        "status": "ok",
        "timestamp": int(time.time()),
        "uptime_seconds": int(time.time() - _START_TIME),
        "service": "api-gateway",
    }


@router.get("/api/health/ready", summary="Readiness")
async def readiness():
    """Readiness — checks all runtime dependencies. Returns 503 if any critical dep is down."""
    settings = get_settings()
    checks: dict[str, dict] = {}
    overall_ok = True

    # ── Redis ────────────────────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        await _redis.ping()
        checks["redis"] = {"status": "ok", "latency_ms": round((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        checks["redis"] = {"status": "error", "detail": str(exc)[:80]}
        overall_ok = False

    # ── Internal services ────────────────────────────────────────────────────
    svc_urls = [
        ("identity_service",  f"{settings.identity_service_url}/health"),
        ("inventory_service", f"{settings.inventory_service_url}/health"),
        ("terminal_service",  f"{settings.terminal_service_url}/health"),
        ("recording_service", f"{settings.recording_service_url}/health"),
    ]
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, url in svc_urls:
            t0 = time.monotonic()
            try:
                resp = await client.get(url)
                ok = resp.status_code == 200
                checks[name] = {
                    "status": "ok" if ok else "degraded",
                    "latency_ms": round((time.monotonic() - t0) * 1000),
                    "http_status": resp.status_code,
                }
                if not ok:
                    overall_ok = False
            except Exception as exc:
                checks[name] = {"status": "error", "detail": str(exc)[:80]}
                overall_ok = False

        # ── Optional services (don't fail readiness if not deployed) ─────────
        optional_urls = [
            ("automation_service",           f"{settings.automation_service_url}/health"),
            ("zabbix_integration_service",   f"{settings.zabbix_integration_service_url}/health"),
        ]
        for name, url in optional_urls:
            t0 = time.monotonic()
            try:
                resp = await client.get(url)
                checks[name] = {
                    "status": "ok" if resp.status_code == 200 else "degraded",
                    "latency_ms": round((time.monotonic() - t0) * 1000),
                    "optional": True,
                }
            except Exception:
                checks[name] = {"status": "unavailable", "optional": True}

    status_code = 200 if overall_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if overall_ok else "degraded",
            "timestamp": int(time.time()),
            "checks": checks,
        },
    )
