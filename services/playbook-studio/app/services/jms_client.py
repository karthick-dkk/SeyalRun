"""JumpServer REST API client — circuit-breaker wrapped, token-authenticated."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from ..config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

_CIRCUIT_STATE: dict[str, Any] = {"open": False, "failures": 0, "threshold": 5}


async def get_assets(token: str) -> list[dict]:
    return await _get("/api/v1/assets/assets/", token)


async def get_asset_nodes(token: str) -> list[dict]:
    return await _get("/api/v1/assets/nodes/", token)


async def get_user_perms(token: str, user_id: str) -> dict:
    return await _get(f"/api/v1/perms/users/{user_id}/assets/", token)


async def run_command(token: str, payload: dict) -> dict:
    return await _post("/api/v1/ops/command-executions/", token, payload)


# ── Internal HTTP helpers ─────────────────────────────────────────────────────


async def _get(path: str, token: str) -> Any:
    if _CIRCUIT_STATE["open"]:
        raise RuntimeError("JumpServer circuit breaker is open")

    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            resp = await client.get(
                f"{settings.jumpserver_api_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            _reset_circuit()
            return resp.json()
    except Exception as exc:
        _record_failure(exc)
        raise


async def _post(path: str, token: str, payload: dict) -> Any:
    if _CIRCUIT_STATE["open"]:
        raise RuntimeError("JumpServer circuit breaker is open")

    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            resp = await client.post(
                f"{settings.jumpserver_api_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            resp.raise_for_status()
            _reset_circuit()
            return resp.json()
    except Exception as exc:
        _record_failure(exc)
        raise


def _record_failure(exc: Exception) -> None:
    _CIRCUIT_STATE["failures"] += 1
    if _CIRCUIT_STATE["failures"] >= _CIRCUIT_STATE["threshold"]:
        _CIRCUIT_STATE["open"] = True
        log.warning("jms_circuit_open", failures=_CIRCUIT_STATE["failures"])

        import asyncio

        asyncio.create_task(_schedule_half_open())

    log.warning("jms_request_failed", error=str(exc))


def _reset_circuit() -> None:
    _CIRCUIT_STATE["open"] = False
    _CIRCUIT_STATE["failures"] = 0


async def _schedule_half_open() -> None:
    import asyncio

    await asyncio.sleep(60)
    _CIRCUIT_STATE["open"] = False
    log.info("jms_circuit_half_open")
