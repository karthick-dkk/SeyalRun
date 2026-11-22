"""Forwards audit entries to identity-service's shared za_audit_logs table.

Every posture API call is audited, reads included (M1 acceptance: "every API
call is audited"). The middleware in main.py writes one row per request; the
handlers that change trust state (feed import, feed activation) additionally
write their own row with critical=True BEFORE committing, so an unrecordable
change is refused rather than made silently.
"""

from __future__ import annotations

import logging

import httpx
from libs.servicetoken import mint

from .config import get_settings

logger = logging.getLogger(__name__)


class AuditWriteError(RuntimeError):
    """The audit entry could not be recorded in the tamper-evident chain."""


async def log_action(
    *,
    user_id: str | None,
    username: str = "",
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    details: dict | None = None,
    ip_address: str = "",
    session_id: str | None = None,
    result: str | None = None,
    critical: bool = False,
) -> None:
    """Forward one entry. ``critical=True`` raises AuditWriteError on failure so
    the caller can refuse the operation instead of proceeding unlogged."""
    settings = get_settings()
    token = mint("posture-service", "identity-service", settings.service_jwt_secret)
    payload = {
        "user_id": user_id or None,
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
            response = await client.post(
                f"{settings.identity_service_url}/api/v1/internal/audit",
                json=payload,
                headers={"X-Service-Token": token},
            )
        # A 4xx/5xx must not read as success — the entry would never get a
        # sequence number and the chain would look intact while incomplete.
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error(
            "audit: failed to forward entry to identity-service",
            extra={"action": action, "resource_id": resource_id, "error": str(exc)},
        )
        if critical:
            raise AuditWriteError(
                f"could not record audit entry for {action!r}; refusing to proceed"
            ) from exc
