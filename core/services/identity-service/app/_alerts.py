"""PCI DSS Phase B — security-event alerts. Fires through automation-service's
notification pipeline (in-app + per-group email, see automation-service/app/
runner.py::create_notification) rather than identity-service growing its own
delivery plumbing. Best-effort: a broken/unreachable automation-service must
never affect the security-relevant action that triggered the alert.
"""

from __future__ import annotations

import logging

import httpx

from libs.servicetoken import mint

from .config import get_settings

logger = logging.getLogger(__name__)


async def fire_security_alert(
    *, severity: str, title: str, message: str = "", source_type: str = "security", source_id: str | None = None,
) -> None:
    settings = get_settings()
    try:
        token = mint("identity-service", "automation-service", settings.service_jwt_secret)
        async with httpx.AsyncClient(base_url=settings.automation_service_url, timeout=5.0) as client:
            await client.post(
                "/api/v1/internal/notifications",
                headers={"X-Service-Token": token},
                json={"severity": severity, "title": title, "message": message,
                      "source_type": source_type, "source_id": source_id},
            )
    except httpx.HTTPError:
        logger.warning("security alert dispatch failed", extra={"event_type": "security_alert", "title": title})
