"""Forwards audit entries to identity-service's shared za_audit_logs table."""

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
    username: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    details: dict | None = None,
    ip_address: str = "",
    session_id: str | None = None,
    result: str | None = None,
    critical: bool = False,
) -> None:
    """Forward one entry to identity-service's hash-chained audit log.

    ``critical=True`` makes a failed write raise instead of warn. Use it where
    proceeding unlogged would defeat the control — releasing a plaintext
    credential, for example: "we handed out the secret but have no record of it"
    is the exact situation PCI DSS Req 10 exists to prevent. The caller is then
    responsible for failing the operation rather than continuing silently.
    """
    settings = get_settings()
    token = mint("inventory-service", "identity-service", settings.service_jwt_secret)
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
            response = await client.post(
                f"{settings.identity_service_url}/api/v1/internal/audit",
                json=payload,
                headers={"X-Service-Token": token},
            )
        # The response status was previously ignored, so identity-service
        # rejecting or failing on an entry looked identical to success.
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
