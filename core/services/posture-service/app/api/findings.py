"""M2 findings API: findings, unmatched queue, scanners, native run, result import.

Reads: anyone the gateway let through for posture GET (posture:read / admin +
support), narrowed to the hosts the caller may see. Imports, runs, assignment
and status changes: admin only, checked on the REAL role (deps.require_admin).
Every call is audited by main.audit_every_call; the ones that change findings
also write a critical audit row BEFORE the change, so an unrecordable change is
refused rather than made.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from libs.pluginbase import ScanParseError
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, clients, feedbundle, feeds, scanning
from ..config import get_settings
from ..database import get_session
from ..deps import Caller, get_caller, require_admin
from ..models import PostureFinding, PostureFindingEvent, PostureHost, PostureUnmatched
from ..refs import safe_urls
from ..risk import SEVERITIES, feed_age_days, feed_stale

router = APIRouter(prefix="/posture", tags=["posture-findings"])

STATUSES = ("open", "fixed", "accepted")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def finding_out(f: PostureFinding, hostname: str) -> dict:
    return {
        "id": f.id, "host_id": f.host_id, "hostname": hostname, "cve_id": f.cve_id, "package": f.package,
        "installed_version": f.installed_version, "fixed_version": f.fixed_version,
        "severity": f.severity if f.severity in SEVERITIES else "unknown",
        "cvss": f.cvss, "epss": f.epss, "epss_percentile": f.epss_percentile, "kev": bool(f.kev),
        "risk_score": int(f.risk_score or 0), "sources": list(f.sources or []),
        "first_seen": _iso(f.first_seen), "last_seen": _iso(f.last_seen), "status": f.status,
        "summary": f.summary,
    }


async def _scope(caller: Caller) -> tuple[set[str] | None, dict[str, str]]:
    """(allowed host ids or None = all, host id -> display name)."""
    hosts = await clients.visible_hosts(caller)
    names = {h["id"]: (h.get("name") or "") for h in hosts}
    allowed = await clients.visible_host_ids(caller)
    return allowed, names


async def _hostnames(session: AsyncSession, names: dict[str, str], host_ids: set[str]) -> dict[str, str]:
    missing = [h for h in host_ids if not names.get(h)]
    if missing:
        res = await session.execute(select(PostureHost.host_id, PostureHost.hostname)
                                    .where(PostureHost.host_id.in_(missing)))
        for hid, hn in res.all():
            names[hid] = hn or names.get(hid, "")
    return names


@router.get("/findings")
async def list_findings(
    request: Request,
    host_id: str | None = None,
    severity: str | None = None,
    kev: bool | None = None,
    status_: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="risk", pattern="^(risk|last_seen)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    caller: Caller = Depends(get_caller),
    session: AsyncSession = Depends(get_session),
):
    if severity is not None and severity not in SEVERITIES:
        raise HTTPException(status_code=400, detail=f"severity must be one of {', '.join(SEVERITIES)}")
    if status_ is not None and status_ not in STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(STATUSES)}")
    allowed, names = await _scope(caller)
    conds = []
    if allowed is not None:
        if not allowed:
            request.state.audit["details"] = {"count": 0}
            return {"items": [], "total": 0}
        conds.append(PostureFinding.host_id.in_(sorted(allowed)))
    if host_id:
        if allowed is not None and host_id not in allowed:
            return {"items": [], "total": 0}
        conds.append(PostureFinding.host_id == host_id)
    if severity:
        conds.append(PostureFinding.severity == severity)
    if kev is not None:
        conds.append(PostureFinding.kev.is_(kev))
    if status_:
        conds.append(PostureFinding.status == status_)
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        host_hits = [hid for hid, n in names.items() if q.strip().lower() in (n or "").lower()]
        ors = [func.lower(PostureFinding.cve_id).like(needle), func.lower(PostureFinding.package).like(needle)]
        if host_hits:
            ors.append(PostureFinding.host_id.in_(host_hits))
        conds.append(or_(*ors))
    where = and_(*conds) if conds else true()
    total = (await session.execute(select(func.count()).select_from(PostureFinding).where(where))).scalar_one()
    order = ([PostureFinding.risk_score.desc(), PostureFinding.last_seen.desc(), PostureFinding.id]
             if sort == "risk" else [PostureFinding.last_seen.desc(), PostureFinding.id])
    rows = (await session.execute(select(PostureFinding).where(where).order_by(*order)
                                  .limit(limit).offset(offset))).scalars().all()
    names = await _hostnames(session, names, {r.host_id for r in rows})
    request.state.audit["details"] = {"count": len(rows), "total": total}
    return {"items": [finding_out(r, names.get(r.host_id, "")) for r in rows], "total": total}


@router.get("/findings/summary")
async def findings_summary(caller: Caller = Depends(get_caller), session: AsyncSession = Depends(get_session)):
    allowed, _ = await _scope(caller)
    stale_after = get_settings().posture_feed_stale_days
    snap = await scanning.active_snapshot(session)
    age = feed_age_days(snap.generated_at) if snap is not None else None
    out = {"open_total": 0, "by_severity": {s: 0 for s in SEVERITIES}, "kev_open": 0, "hosts_affected": 0,
           "feed_stale": feed_stale(age, stale_after), "feed_age_days": age, "stale_after_days": stale_after}
    if allowed is not None and not allowed:
        return out
    conds = [PostureFinding.status == "open"]
    if allowed is not None:
        conds.append(PostureFinding.host_id.in_(sorted(allowed)))
    for sev, n in (await session.execute(select(PostureFinding.severity, func.count())
                                         .where(*conds).group_by(PostureFinding.severity))).all():
        key = sev if sev in SEVERITIES else "unknown"
        out["by_severity"][key] += n
        out["open_total"] += n
    out["kev_open"] = (await session.execute(select(func.count()).select_from(PostureFinding)
                                             .where(*conds, PostureFinding.kev.is_(True)))).scalar_one()
    out["hosts_affected"] = (await session.execute(
        select(func.count(func.distinct(PostureFinding.host_id))).where(*conds))).scalar_one()
    return out


async def _visible_finding(session: AsyncSession, caller: Caller, finding_id: str) -> PostureFinding:
    f = await session.get(PostureFinding, finding_id)
    if f is None:
        raise HTTPException(status_code=404, detail="finding not found")
    allowed = await clients.visible_host_ids(caller)
    if allowed is not None and f.host_id not in allowed:
        raise HTTPException(status_code=404, detail="finding not found")
    return f


async def _one_hostname(session: AsyncSession, host_id: str) -> str:
    row = await session.get(PostureHost, host_id)
    if row is not None and row.hostname:
        return row.hostname
    inv = await clients.inventory_host(host_id)
    return (inv or {}).get("name") or ""


async def _detail(session: AsyncSession, f: PostureFinding) -> dict:
    out = finding_out(f, await _one_hostname(session, f.host_id))
    # Filtered again on the way out: rows written before a tightening, or by any
    # path that missed the parse-time filter, still never reach the UI as links.
    out["references"] = safe_urls(f.references or [])
    events = (await session.execute(select(PostureFindingEvent).where(PostureFindingEvent.finding_id == f.id)
                                    .order_by(PostureFindingEvent.at, PostureFindingEvent.id).limit(500))).scalars().all()
    out["history"] = [{"at": _iso(e.at), "event": e.event, "by": e.by, "details": e.details or {}} for e in events]
    out["status_reason"] = f.status_reason
    out["fixed_at"] = _iso(f.fixed_at)
    return out


@router.get("/findings/{finding_id}")
async def get_finding(finding_id: str, caller: Caller = Depends(get_caller),
                      session: AsyncSession = Depends(get_session)):
    f = await _visible_finding(session, caller, finding_id)
    return await _detail(session, f)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(open|accepted)$")
    reason: str = Field(default="", max_length=1000)


@router.post("/findings/{finding_id}/status")
async def set_finding_status(finding_id: str, body: StatusIn, request: Request,
                             caller: Caller = Depends(require_admin),
                             session: AsyncSession = Depends(get_session)):
    f = await _visible_finding(session, caller, finding_id)
    reason = body.reason.strip()
    if body.status == "accepted" and not reason:
        raise HTTPException(status_code=400, detail="a reason is required to accept a finding's risk")
    previous = f.status
    request.state.audit["details"] = {"from": previous, "to": body.status, "cve_id": f.cve_id, "host_id": f.host_id}
    t = scanning.now()
    f.status = body.status
    f.status_reason = reason or None
    if body.status == "open":
        f.fixed_at = None
    session.add(PostureFindingEvent(finding_id=f.id, at=t, event=f"status:{body.status}",
                                    by=(caller.username or caller.user_id)[:200],
                                    details={"from": previous, "reason": reason[:1000]}))
    await session.flush()
    try:
        await audit.log_action(
            user_id=caller.user_id, username=caller.username, action="posture.finding.status",
            resource_type="finding", resource_id=f.id, ip_address=caller.ip,
            details={**request.state.audit["details"], "reason": reason[:500]}, result="success", critical=True)
    except audit.AuditWriteError as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail="audit log unavailable; status change refused") from exc
    await session.commit()
    await session.refresh(f)
    request.state.audited = True
    return finding_out(f, await _one_hostname(session, f.host_id))


# ── unmatched queue ──────────────────────────────────────────────────────────

@router.get("/unmatched")
async def list_unmatched(request: Request, limit: int = Query(default=50, ge=1, le=500),
                         offset: int = Query(default=0, ge=0), caller: Caller = Depends(get_caller),
                         session: AsyncSession = Depends(get_session)):
    # Results for hosts not in inventory belong to no host scope, so only
    # callers who see every host see the queue.
    if not caller.sees_all_hosts:
        return {"items": [], "total": 0}
    where = PostureUnmatched.status == "pending"
    total = (await session.execute(select(func.count()).select_from(PostureUnmatched).where(where))).scalar_one()
    rows = (await session.execute(select(PostureUnmatched).where(where)
                                  .order_by(PostureUnmatched.received_at.desc(), PostureUnmatched.id)
                                  .limit(limit).offset(offset))).scalars().all()
    request.state.audit["details"] = {"count": len(rows), "total": total}
    return {"items": [{"id": u.id, "scanner": u.scanner, "reported_host": u.reported_host,
                       "reported_ip": u.reported_ip, "cve_count": u.cve_count,
                       "received_at": _iso(u.received_at), "scan_id": u.scan_id} for u in rows],
            "total": total}


class AssignIn(BaseModel):
    host_id: str = Field(min_length=1, max_length=36)


@router.post("/unmatched/{unmatched_id}/assign")
async def assign_unmatched(unmatched_id: str, body: AssignIn, request: Request,
                           caller: Caller = Depends(require_admin),
                           session: AsyncSession = Depends(get_session)):
    u = await session.get(PostureUnmatched, unmatched_id)
    if u is None:
        raise HTTPException(status_code=404, detail="unmatched result not found")
    if u.status != "pending":
        raise HTTPException(status_code=409, detail=f"already assigned to host {u.assigned_host_id}")
    if await clients.inventory_host(body.host_id) is None:
        raise HTTPException(status_code=404, detail="host not found")
    details = {"host_id": body.host_id, "scanner": u.scanner, "reported_host": u.reported_host,
               "cve_count": u.cve_count}
    request.state.audit["details"] = details
    try:
        await audit.log_action(user_id=caller.user_id, username=caller.username,
                               action="posture.unmatched.assign", resource_type="unmatched",
                               resource_id=unmatched_id, ip_address=caller.ip, details=details,
                               result="accepted", critical=True)
    except audit.AuditWriteError as exc:
        raise HTTPException(status_code=503, detail="audit log unavailable; assignment refused") from exc
    await session.close()
    created = await scanning.assign_unmatched(unmatched_id, body.host_id, caller.user_id, caller.username)
    request.state.audit["details"]["created_findings"] = created
    return {"created_findings": created}


# ── scanners ─────────────────────────────────────────────────────────────────

@router.get("/scanners")
async def list_scanners(request: Request, _caller: Caller = Depends(get_caller)):
    reg = scanning.registry()
    disabled = await clients.disabled_scanners()
    runs = await scanning.last_runs()
    out = []
    for name in sorted(reg, key=lambda n: (reg[n].kind != "builtin", n)):
        s = reg[name]
        at, st = runs.get(name, (None, None))
        out.append({"name": name, "kind": s.kind if s.kind in ("builtin", "plugin") else "plugin",
                    "accepts_upload": bool(s.accepts_upload), "formats": list(s.formats),
                    "enabled": name not in disabled, "last_run_at": _iso(at), "last_status": st})
    request.state.audit["details"] = {"count": len(out)}
    return out


class NativeRunIn(BaseModel):
    host_ids: list[str] | None = Field(default=None, max_length=5000)


@router.post("/scanners/native/run", status_code=status.HTTP_202_ACCEPTED)
async def run_native(request: Request, body: NativeRunIn | None = Body(default=None),
                     caller: Caller = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    if "native" not in scanning.registry():
        raise HTTPException(status_code=400, detail="native scanner is not installed")
    if "native" in await clients.disabled_scanners():
        raise HTTPException(status_code=400, detail="the native scanner is disabled on the Integration page")
    allowed = await clients.visible_host_ids(caller)
    with_facts = set(await scanning.hosts_with_facts())
    wanted = list(dict.fromkeys(body.host_ids)) if body and body.host_ids else sorted(with_facts)
    targets = [h for h in wanted if h in with_facts and (allowed is None or h in allowed)]
    skipped = [h for h in wanted if h not in targets]
    scanning.enqueue_native(targets, caller.user_id, caller.username)
    request.state.audit["details"] = {"queued": len(targets), "skipped": len(skipped), "host_ids": targets[:200]}
    return {"queued": len(targets), "skipped_no_facts": skipped[:200]}


# ── import ───────────────────────────────────────────────────────────────────

async def _save_upload(upload: UploadFile, dest: Path, max_bytes: int) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(dest, "xb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(status_code=413, detail=f"file exceeds POSTURE_SCAN_MAX_BYTES ({max_bytes} bytes)")
            h.update(chunk)
            await asyncio.to_thread(out.write, chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="empty upload")
    return h.hexdigest(), size


@router.post("/scans/import", status_code=status.HTTP_201_CREATED)
async def import_scan(request: Request, file: UploadFile = File(...), scanner: str = Form(...),
                      host_id: str | None = Form(default=None),
                      caller: Caller = Depends(require_admin)):
    settings = get_settings()
    ad = request.state.audit
    ad["details"] = {"scanner": scanner[:64], "filename": (file.filename or "")[:200],
                     **({"host_id": host_id[:36]} if host_id else {})}
    plugin = scanning.registry().get(scanner)
    if plugin is None or not plugin.accepts_upload:
        names = sorted(n for n, p in scanning.registry().items() if p.accepts_upload)
        raise HTTPException(status_code=400, detail=f"scanner must be one of: {', '.join(names)}")
    if scanner in await clients.disabled_scanners():
        raise HTTPException(status_code=400, detail=f"scanner '{scanner}' is disabled on the Integration page")
    host_id = (host_id or "").strip() or None
    if host_id and await clients.inventory_host(host_id) is None:
        raise HTTPException(status_code=400, detail=f"host_id {host_id} is not an inventory host")

    work = feeds.new_work_dir()
    upload, spool = work.with_suffix(".scan"), work.with_suffix(".jsonl")
    scan_id = None
    try:
        sha, size = await _save_upload(file, upload, settings.posture_scan_max_bytes)
        ad["details"].update({"sha256": sha, "size": size})
        scan_id = await scanning.start_scan(scanner, "import", host_id=host_id, filename=file.filename or "",
                                            sha256=sha, user_id=caller.user_id, username=caller.username)
        ad["details"]["scan_id"] = scan_id
        try:
            n = await asyncio.to_thread(scanning.spool_records, plugin, upload, spool,
                                        settings.posture_scan_max_records)
        except ScanParseError as exc:
            ad["details"]["reason"] = str(exc)[:500]
            await scanning.finish_scan(scan_id, "failed", str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            upload.unlink(missing_ok=True)
        ad["details"]["records"] = n
        try:
            await audit.log_action(user_id=caller.user_id, username=caller.username, action="posture.scan.import",
                                   resource_type="scan", resource_id=scan_id, ip_address=caller.ip,
                                   details=dict(ad["details"]), result="accepted", critical=True)
        except audit.AuditWriteError as exc:
            await scanning.finish_scan(scan_id, "failed", "audit log unavailable")
            raise HTTPException(status_code=503, detail="audit log unavailable; import refused") from exc
        counts = await scanning.apply_import(scan_id, scanner, spool, host_id, caller.username or caller.user_id)
        await scanning.finish_scan(scan_id, "ok", **counts)
        ad["details"].update(counts)
        return {"scan_id": scan_id, **counts}
    except HTTPException:
        raise
    except Exception as exc:
        if scan_id:
            await scanning.finish_scan(scan_id, "failed", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        upload.unlink(missing_ok=True)
        spool.unlink(missing_ok=True)
        feedbundle.remove_tree(work)
