"""GET/POST posture/hosts..., POST posture/collect, GET posture/summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import clients, collector
from ..config import get_settings
from ..database import get_session
from ..deps import Caller, get_caller, require_admin
from ..models import (
    FeedSnapshot,
    PostureFinding,
    PostureHost,
    PostureInstalledPackage,
    PosturePendingUpdate,
)

router = APIRouter(prefix="/posture", tags=["posture"])


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def host_status(row: PostureHost | None, stale_hours: int, now: datetime | None = None) -> str:
    """ok | stale | failed | never — from the latest attempt and the data's age."""
    if row is None:
        return "never"
    if row.last_result == "failed":
        return "failed"
    if row.collected_at is None:
        return "never"
    collected = row.collected_at if row.collected_at.tzinfo else row.collected_at.replace(tzinfo=UTC)
    if (now or datetime.now(UTC)) - collected > timedelta(hours=stale_hours):
        return "stale"
    return "ok"


def host_out(inv: dict, row: PostureHost | None, stale_hours: int) -> dict:
    os_family = clients.os_family(inv.get("os_type"))
    r = row
    return {
        "host_id": inv["id"],
        "hostname": (r.hostname if r and r.hostname else inv.get("name") or ""),
        "os_family": os_family,
        "distro": r.distro if r else "",
        "distro_version": r.distro_version if r else "",
        "kernel": r.kernel if r else "",
        "arch": r.arch if r else "",
        "packages_installed": r.packages_installed if r else 0,
        "pending_updates": r.pending_updates if r else 0,
        "security_updates": r.security_updates if r else 0,
        "reboot_required": bool(r.reboot_required) if r else False,
        "collected_at": _iso(r.collected_at) if r else None,
        "status": host_status(r, stale_hours),
        "last_error": r.last_error if r else None,
        # Additive to the fixed contract.
        "name": inv.get("name") or "",
        "distro_id": r.distro_id if r else "",
        "distro_family": r.distro_family if r else "",
        "reboot_required_known": bool(r and r.reboot_required is not None),
        "reboot_method": r.reboot_method if r else "",
        "collect_state": r.collect_state if r else "idle",
        "last_job_run_id": r.last_job_run_id if r else None,
        "warnings": list(r.warnings or []) if r else [],
    }


async def _rows(session: AsyncSession, host_ids: list[str]) -> dict[str, PostureHost]:
    if not host_ids:
        return {}
    res = await session.execute(select(PostureHost).where(PostureHost.host_id.in_(host_ids)))
    return {r.host_id: r for r in res.scalars().all()}


@router.get("/hosts")
async def list_hosts(request: Request, caller: Caller = Depends(get_caller),
                     session: AsyncSession = Depends(get_session)):
    hosts = await clients.visible_hosts(caller)
    rows = await _rows(session, [h["id"] for h in hosts])
    stale = get_settings().posture_stale_hours
    out = [host_out(h, rows.get(h["id"]), stale) for h in hosts]
    request.state.audit["details"] = {"count": len(out)}
    return out


async def _visible_inventory_host(caller: Caller, host_id: str) -> dict:
    """404 both for an unknown host and for one the caller may not see — the
    response must not reveal that a host exists outside the caller's scope."""
    allowed = await clients.visible_host_ids(caller)
    if allowed is not None and host_id not in allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    inv = await clients.inventory_host(host_id)
    if inv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    return inv


@router.get("/hosts/{host_id}")
async def get_host(host_id: str, caller: Caller = Depends(get_caller),
                   limit: int = Query(default=1000, ge=1, le=5000),
                   offset: int = Query(default=0, ge=0),
                   session: AsyncSession = Depends(get_session)):
    inv = await _visible_inventory_host(caller, host_id)
    row = await session.get(PostureHost, host_id)
    out = host_out(inv, row, get_settings().posture_stale_hours)
    pend = await session.execute(
        select(PosturePendingUpdate).where(PosturePendingUpdate.host_id == host_id)
        .order_by(PosturePendingUpdate.security.desc(), PosturePendingUpdate.name))
    out["packages_pending"] = [
        {"name": p.name, "current": p.current, "available": p.available, "security": bool(p.security),
         "arch": p.arch, "source": p.source}
        for p in pend.scalars().all()
    ]
    inst = await session.execute(
        select(PostureInstalledPackage).where(PostureInstalledPackage.host_id == host_id)
        .order_by(PostureInstalledPackage.name, PostureInstalledPackage.arch).limit(limit).offset(offset))
    out["packages_installed_list"] = [{"name": p.name, "version": p.version, "arch": p.arch}
                                      for p in inst.scalars().all()]
    out["packages_installed_limit"] = limit
    out["packages_installed_offset"] = offset
    return out


def _collectable(inv: dict) -> str | None:
    """Why this host cannot be collected, or None."""
    if clients.os_family(inv.get("os_type")) != "linux":
        return "Windows facts collection is not available yet"
    if inv.get("enabled") is False:
        return "host is disabled in inventory"
    return None


@router.post("/hosts/{host_id}/collect", status_code=status.HTTP_202_ACCEPTED)
async def collect_host(host_id: str, request: Request, caller: Caller = Depends(require_admin)):
    inv = await _visible_inventory_host(caller, host_id)
    reason = _collectable(inv)
    if reason:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)
    runs = await collector.request_collection([inv], caller.user_id)
    if not runs:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="automation-service returned no run")
    request.state.audit["details"] = {"job_run_id": runs[0]["run_id"], "job_status": runs[0].get("status")}
    return {"job_run_id": runs[0]["run_id"], "status": runs[0].get("status")}


@router.post("/collect", status_code=status.HTTP_202_ACCEPTED)
async def collect_all(request: Request, caller: Caller = Depends(require_admin)):
    hosts = await clients.visible_hosts(caller)
    targets, skipped = [], []
    for h in hosts:
        reason = _collectable(h)
        if reason:
            skipped.append({"host_id": h["id"], "reason": reason})
        else:
            targets.append(h)
    runs: list[dict] = []
    for i in range(0, len(targets), 500):
        runs += await collector.request_collection(targets[i:i + 500], caller.user_id)
    ids = [r["run_id"] for r in runs]
    request.state.audit["details"] = {"hosts": len(targets), "skipped": len(skipped), "job_run_ids": ids[:200]}
    return {"job_run_ids": ids, "skipped": skipped}


@router.get("/summary")
async def summary(caller: Caller = Depends(get_caller), session: AsyncSession = Depends(get_session)):
    hosts = await clients.visible_hosts(caller)
    rows = await _rows(session, [h["id"] for h in hosts])
    stale = get_settings().posture_stale_hours
    reporting = reboot = security = 0
    for h in hosts:
        r = rows.get(h["id"])
        if host_status(r, stale) == "ok":
            reporting += 1
        if r is not None and r.collected_at is not None:
            reboot += 1 if r.reboot_required else 0
            security += r.security_updates or 0
    active = (await session.execute(select(FeedSnapshot).where(FeedSnapshot.active.is_(True)).limit(1))).scalar_one_or_none()
    age = None
    if active is not None and active.generated_at is not None:
        gen = active.generated_at if active.generated_at.tzinfo else active.generated_at.replace(tzinfo=UTC)
        age = max(0, (datetime.now(UTC) - gen).days)
    open_q = [PostureFinding.status == "open"]
    if not caller.sees_all_hosts:
        open_q.append(PostureFinding.host_id.in_([h["id"] for h in hosts] or [""]))
    findings_open = (await session.execute(select(func.count()).select_from(PostureFinding).where(*open_q))).scalar_one()
    kev_open = (await session.execute(select(func.count()).select_from(PostureFinding)
                                      .where(*open_q, PostureFinding.kev.is_(True)))).scalar_one()
    return {
        "hosts_total": len(hosts),
        "hosts_reporting": reporting,
        "reboot_required": reboot,
        "security_updates": security,
        "active_feed_version": active.version if active else None,
        "feed_age_days": age,
        # Additive: how many feed snapshots exist at all.
        "feed_snapshots": (await session.execute(select(func.count()).select_from(FeedSnapshot))).scalar_one(),
        # M2
        "findings_open": findings_open,
        "kev_open": kev_open,
    }
