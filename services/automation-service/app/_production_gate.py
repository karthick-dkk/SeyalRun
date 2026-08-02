"""Production-host gating (PCI DSS Phase D) — checks inventory-service for any
target host tagged is_production, so a dispatch path can force the approval
flow regardless of the template's own requires_approval setting. Shared by
every entry point that creates a ZAJobRun directly (job_templates.py's manual
run, scheduler.py's cron dispatch, internal.py's Zabbix-triggered webhook) so
"production is always gated" holds regardless of trigger mechanism — a single
copy in job_templates.py only covered the manual-run path.

chain.py's per-step dispatch is gated differently than the other three: a
chain step is awaited synchronously as part of one continuous run (the chain
loop blocks on each step's result before starting the next), so a step
landing in pending_approval would leave the whole chain hanging indefinitely
with nothing to resume it. Rather than build a suspend/resume mechanism,
ChainExecutor.execute() checks every step's target hosts up front and
refuses to run the WHOLE chain if any step targets production — the human
must run that specific step individually, through run_template's own
approval gate, instead.
"""

from __future__ import annotations

import httpx
from libs.servicetoken import mint

from .config import get_settings


async def any_production_hosts(host_ids: list[str]) -> bool:
    if not host_ids:
        return False
    settings = get_settings()
    headers = {
        "X-Service-Token": mint("automation-service", "inventory-service", settings.service_jwt_secret),
        "X-User-Role": "admin",
    }
    try:
        async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=10) as client:
            r = await client.post("/api/v1/internal/hosts/check-production", json=host_ids, headers=headers)
        if r.status_code >= 400:
            # Fail closed: an unreachable/erroring inventory-service must not silently
            # skip the production gate — treat as "can't confirm safe" rather than "safe".
            return True
        return bool(r.json().get("production_host_ids"))
    except httpx.HTTPError:
        return True
