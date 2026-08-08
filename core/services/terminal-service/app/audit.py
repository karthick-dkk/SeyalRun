"""Forwards audit entries to identity-service's shared za_audit_logs table."""

from __future__ import annotations

import logging

import httpx

from libs.servicetoken import mint

from .config import get_settings

logger = logging.getLogger(__name__)


async def log_action(
    *,
    user_id: str | None,
    username: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    details: dict | None = None,
    ip_address: str = "",
    session_id: str | None = None,
    result: str | None = None,
) -> None:
    settings = get_settings()
    token = mint("terminal-service", "identity-service", settings.service_jwt_secret)
    payload = {
        "user_id": user_id,
        "username": username,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "details": details or {},
        "ip_address": ip_address,
        "session_id": session_id,
        "result": result,
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{settings.identity_service_url}/api/v1/internal/audit",
                json=payload,
                headers={"X-Service-Token": token},
            )
    except httpx.HTTPError:
        logger.warning("audit: failed to forward to identity-service", extra={"action": action})
