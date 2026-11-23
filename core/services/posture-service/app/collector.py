"""Host facts collection: request facts runs from automation-service, then poll
them and ingest the parsed output.

Why poll rather than a callback: automation-service stays ignorant of posture
(no new outbound dependency, no posture URL in its config), the in-flight state
lives in posture_hosts so a posture-service restart simply resumes polling, and
the loop only does work while some host actually has a run outstanding.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, insert, select

from . import audit, clients, facts
from .config import get_settings
from .database import SessionLocal
from .models import PostureHost, PostureInstalledPackage, PosturePendingUpdate

logger = logging.getLogger(__name__)

# automation-service run statuses
_DONE = {"success", "failed", "error", "cancelled", "rejected", "timeout"}
_STATE_FOR = {"pending": "queued", "running": "running", "pending_approval": "awaiting_approval"}
IN_FLIGHT = ("queued", "running", "awaiting_approval")


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def request_collection(hosts: list[dict], user_id: str) -> list[dict]:
    """hosts: inventory host dicts (already visibility- and OS-filtered).
    Returns automation's [{host_id, run_id, status, reused}]."""
    if not hosts:
        return []
    runs = await clients.request_facts_runs([h["id"] for h in hosts], user_id)
    by_id = {h["id"]: h for h in hosts}
    now = _now()
    async with SessionLocal() as session:
        for run in runs:
            hid = run["host_id"]
            row = await session.get(PostureHost, hid)
            if row is None:
                inv = by_id.get(hid, {})
                row = PostureHost(host_id=hid, hostname=inv.get("name") or "",
                                  os_family=clients.os_family(inv.get("os_type")), warnings=[])
                session.add(row)
            row.last_job_run_id = run["run_id"]
            row.collect_state = _STATE_FOR.get(run.get("status"), "queued")
            row.last_requested_at = now
            row.last_requested_by = user_id
        await session.commit()
    return runs


async def ingest(host_id: str, run_id: str, run: dict) -> None:
    """Fold one finished facts run into posture_hosts (+ package tables)."""
    lines = run.get("output_lines") or []
    status = run.get("status")
    parsed: dict | None = None
    error: str | None = None
    try:
        parsed = facts.parse_facts(facts.extract_payload(lines))
    except facts.FactsError as exc:
        if status == "success":        # the job ran but its output is unusable
            error = str(exc)
        elif status in ("rejected", "cancelled"):
            error = f"facts run {status}"
        else:                          # SSH/credential/host failure: say what the job said
            error = facts.last_error_line(lines)
    async with SessionLocal() as session:
        row = await session.get(PostureHost, host_id)
        if row is None or row.last_job_run_id != run_id:
            return  # superseded by a newer request; that run will be ingested instead
        row.collect_state = "idle"
        if parsed is None:
            row.last_result = "failed"
            row.last_error = (error or "facts collection failed")[:2000]
        else:
            row.hostname = parsed["hostname"][:255] or row.hostname
            row.os_family = "linux"
            row.distro = parsed["distro"][:128]
            row.distro_id = parsed["distro_id"][:64]
            row.distro_family = parsed["distro_family"][:32]
            row.distro_version = parsed["distro_version"][:64]
            row.kernel = parsed["kernel"][:128]
            row.arch = parsed["arch"][:32]
            row.pkg_manager = parsed["pkg_manager"][:16]
            row.packages_installed = len(parsed["installed"])
            row.pending_updates = len(parsed["pending"])
            row.security_updates = parsed["security_updates"]
            row.reboot_required = parsed["reboot_required"]
            row.reboot_method = parsed["reboot_method"][:64]
            row.warnings = parsed["warnings"][:20]
            row.collected_at = _now()
            row.last_result = "ok"
            row.last_error = None
            await session.execute(delete(PostureInstalledPackage).where(PostureInstalledPackage.host_id == host_id))
            await session.execute(delete(PosturePendingUpdate).where(PosturePendingUpdate.host_id == host_id))
            pkgs = [{"host_id": host_id, "name": p["name"][:255], "version": p["version"][:255],
                     "arch": p["arch"][:32], "source": (p.get("source") or "")[:255],
                     "source_version": (p.get("source_version") or "")[:255]} for p in parsed["installed"]]
            for i in range(0, len(pkgs), 2000):
                await session.execute(insert(PostureInstalledPackage), pkgs[i:i + 2000])
            pend = [{"host_id": host_id, "name": p["name"][:255], "arch": p["arch"][:32],
                     "current": p["current"][:255], "available": p["available"][:255],
                     "security": bool(p["security"]), "source": p["source"][:128]} for p in parsed["pending"]]
            if pend:
                await session.execute(insert(PosturePendingUpdate), pend)
        await session.commit()
    await audit.log_action(
        user_id=None, username="posture-service", action="posture.facts.ingest",
        resource_type="host", resource_id=host_id,
        details={"job_run_id": run_id, "job_status": status,
                 **({"packages": len(parsed["installed"]), "pending": len(parsed["pending"]),
                     "security": parsed["security_updates"]} if parsed else {"error": (error or "")[:300]})},
        result="success" if parsed else "failure",
    )
    if parsed:
        # M2: every successful facts collection is followed by a native match.
        from . import scanning
        scanning.enqueue_native([host_id], requested_by=None, requested_by_name="facts collection")


async def poll_once() -> int:
    """Advance every in-flight host by one poll. Returns how many are still in flight."""
    settings = get_settings()
    async with SessionLocal() as session:
        res = await session.execute(select(PostureHost).where(PostureHost.collect_state.in_(IN_FLIGHT)))
        hosts = [(h.host_id, h.last_job_run_id, h.collect_state, _aware(h.last_requested_at))
                 for h in res.scalars().all()]
    remaining = 0
    deadline = timedelta(minutes=settings.posture_collect_timeout_minutes)
    for host_id, run_id, state, requested_at in hosts:
        if not run_id:
            continue
        try:
            run = await clients.job_run(run_id)
        except Exception as exc:
            logger.warning("poll of facts run %s failed: %s", run_id, type(exc).__name__)
            remaining += 1
            continue
        if run is None:
            await _fail(host_id, run_id, "facts job run no longer exists in automation-service")
            continue
        status = run.get("status")
        if status in _DONE:
            await ingest(host_id, run_id, run)
            continue
        new_state = _STATE_FOR.get(status, state)
        overdue = (new_state != "awaiting_approval" and requested_at is not None
                   and _now() - requested_at > deadline)
        if overdue:
            await _fail(host_id, run_id, f"facts run still '{status}' after "
                                         f"{settings.posture_collect_timeout_minutes} min — gave up waiting")
            continue
        if new_state != state:
            async with SessionLocal() as session:
                row = await session.get(PostureHost, host_id)
                if row is not None and row.last_job_run_id == run_id:
                    row.collect_state = new_state
                    await session.commit()
        remaining += 1
    return remaining


async def _fail(host_id: str, run_id: str, reason: str) -> None:
    async with SessionLocal() as session:
        row = await session.get(PostureHost, host_id)
        if row is None or row.last_job_run_id != run_id:
            return
        row.collect_state = "idle"
        row.last_result = "failed"
        row.last_error = reason
        await session.commit()


async def poll_loop() -> None:
    interval = max(1, get_settings().posture_poll_interval_seconds)
    while True:
        try:
            await poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("facts poll iteration failed")
        await asyncio.sleep(interval)
