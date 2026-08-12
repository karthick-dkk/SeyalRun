"""FastAPI health router — /health (liveness) and /ready (readiness)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, HTTPException, status

router = APIRouter(tags=["health"])

# Registry for readiness checks — each service adds its own checks
_readiness_checks: list[tuple[str, Callable[[], Coroutine[Any, Any, bool]]]] = []


def register_readiness_check(
    name: str,
    check: Callable[[], Coroutine[Any, Any, bool]],
) -> None:
    """Register an async check function for /ready.

    check() must return True (healthy) or raise an exception (unhealthy).
    """
    _readiness_checks.append((name, check))


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — always 200 if the process is alive."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict[str, Any]:
    """Readiness probe — checks all registered dependencies."""
    if not _readiness_checks:
        return {"status": "ready", "checks": {}}

    results: dict[str, str] = {}
    failed: list[str] = []

    async def run_check(name: str, check: Callable[[], Coroutine[Any, Any, bool]]) -> None:
        try:
            await asyncio.wait_for(check(), timeout=5.0)
            results[name] = "ok"
        except Exception as exc:
            results[name] = f"failed: {exc}"
            failed.append(name)

    await asyncio.gather(*[run_check(n, c) for n, c in _readiness_checks])

    if failed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "checks": results, "failed": failed},
        )

    return {"status": "ready", "checks": results, "uptime_seconds": int(time.monotonic())}
