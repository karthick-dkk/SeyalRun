"""M3/M4 API: mirrors, package operations, windows, baselines, rollouts.

Reads: any caller the gateway let through for posture GET, narrowed to visible
hosts. Writes: admin only, on the REAL role. Anything that can change a host also
writes a critical audit row first, so an unrecordable change is refused.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from libs.adminapproval import NOT_REQUIRED
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, clients, facts, rollout_core as core, rollout_engine as engine, scanning
from ..config import admin_approval_required, get_settings
from ..database import get_session
from ..deps import Caller, get_caller, require_admin, require_fleet_admin
from ..models import (
    PackageOp,
    PatchBaseline,
    PatchRepo,
    PatchRollout,
    PatchWindow,
    PostureFinding,
    PostureHost,
    PostureInstalledPackage,
    PosturePendingUpdate,
    RolloutEvent,
    RolloutHost,
)
from .hosts import _visible_inventory_host

router = APIRouter(prefix="/posture", tags=["posture-patching"])

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}\Z")
_URL = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/%+$-]*)?\Z")
# The same rule automation-service applies when it writes the key to a host.
_KEY = re.compile(r"-----BEGIN PGP PUBLIC KEY BLOCK-----[A-Za-z0-9+/=\s:.,()@<>_-]+-----END PGP PUBLIC KEY BLOCK-----\s*\Z")
_SUITE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}\Z")
MAX_ROLLOUT_HOSTS = 1000
# A reboot from the host page waits this long for the host to come back (as a rollout does by default).
REBOOT_WAIT_SECONDS = 900
FAMILY = {"dpkg": "deb", "rpm": "rpm"}


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def _bad(detail: str, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _editors(existing: list | None, user_id: str) -> list[str]:
    out = list(existing or [])
    return out if user_id in out else [*out, user_id]


async def _audit_critical(request: Request, caller: Caller, action: str, rtype: str, rid: str, details: dict) -> None:
    try:
        await audit.log_action(user_id=caller.user_id, username=caller.username, action=action, resource_type=rtype,
                               resource_id=rid[:36], details=details, ip_address=caller.ip[:64], result="success", critical=True)
    except audit.AuditWriteError as exc:
        raise _bad("the change could not be recorded in the audit log and was not made", 503) from exc
    request.state.audited = True


async def _commit_unique(session: AsyncSession, what: str) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _bad(f"{what} already exists", 409) from exc


async def _get(session: AsyncSession, model, oid: str, what: str):
    row = await session.get(model, oid)
    if row is None:
        raise _bad(f"{what} not found", 404)
    return row


# ── mirrors ──────────────────────────────────────────────────────────────────

class RepoIn(BaseModel):
    slug: str = Field(max_length=40)
    family: str = Field(pattern="^(deb|rpm|zypp)$")
    base_url: str = Field(max_length=500)
    suite: str = Field(default="", max_length=80)
    components: str = Field(default="", max_length=200)
    gpg_key: str = Field(max_length=32768)
    group_ids: list[str] = Field(default_factory=list, max_length=200)
    enabled: bool = True


def _repo_out(r: PatchRepo) -> dict:
    return {"id": r.id, "slug": r.slug, "family": r.family, "base_url": r.base_url, "suite": r.suite,
            "components": r.components, "gpg_key": r.gpg_key, "group_ids": list(r.group_ids or []),
            "enabled": bool(r.enabled), "updated_at": _iso(r.updated_at)}


def _check_repo(p: RepoIn) -> None:
    if not _SLUG.match(p.slug):
        raise _bad("slug: lower-case letters, digits and hyphens")
    if not _URL.match(p.base_url):
        raise _bad("base_url must be a plain http(s) URL without credentials")
    if p.family == "deb":
        if not _SUITE.match(p.suite) or not all(_SUITE.match(c) for c in p.components.split()):
            raise _bad("a deb repo needs a valid suite and components")
        if not p.suite.endswith("/") and not p.components.split():
            raise _bad("a deb repo needs at least one component")
    if not _KEY.match(p.gpg_key.strip() + "\n"):
        raise _bad("an ASCII-armoured OpenPGP public key is required; unsigned mirrors are not supported")


@router.get("/patch-repos")
async def list_repos(caller: Caller = Depends(get_caller), session: AsyncSession = Depends(get_session)):
    return [_repo_out(r) for r in (await session.execute(select(PatchRepo).order_by(PatchRepo.slug))).scalars().all()]


@router.post("/patch-repos", status_code=201)
async def create_repo(payload: RepoIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                      session: AsyncSession = Depends(get_session)):
    _check_repo(payload)
    row = PatchRepo(id=str(uuid.uuid4()), **payload.model_dump(), editor_ids=[caller.user_id])
    await _audit_critical(request, caller, "posture.patch_repo.create", "patch_repo", row.id,
                          {"slug": row.slug, "base_url": row.base_url, "group_ids": row.group_ids})
    session.add(row)
    await _commit_unique(session, "a repo with this slug")
    return _repo_out(row)


@router.put("/patch-repos/{repo_id}")
async def update_repo(repo_id: str, payload: RepoIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                      session: AsyncSession = Depends(get_session)):
    row = await _get(session, PatchRepo, repo_id, "repo")
    _check_repo(payload)
    await _audit_critical(request, caller, "posture.patch_repo.update", "patch_repo", row.id,
                          {"slug": payload.slug, "base_url": payload.base_url, "group_ids": payload.group_ids})
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    row.editor_ids = _editors(row.editor_ids, caller.user_id)
    await _commit_unique(session, "a repo with this slug")
    return _repo_out(row)


@router.delete("/patch-repos/{repo_id}", status_code=204)
async def delete_repo(repo_id: str, request: Request, caller: Caller = Depends(require_fleet_admin),
                      session: AsyncSession = Depends(get_session)):
    row = await _get(session, PatchRepo, repo_id, "repo")
    await _audit_critical(request, caller, "posture.patch_repo.delete", "patch_repo", row.id, {"slug": row.slug})
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


async def repos_for(session: AsyncSession, inv: dict, host: PostureHost | None) -> tuple[list[dict], list[str]]:
    """(repo definitions for this host, ids of everyone who edited them)."""
    fam = "deb" if host is not None and host.pkg_manager == "dpkg" else \
        ("zypp" if host is not None and host.distro_family == "suse" else "rpm")
    groups = set(inv.get("group_ids") or [])
    rows = (await session.execute(select(PatchRepo).where(PatchRepo.enabled.is_(True), PatchRepo.family == fam)
                                  .order_by(PatchRepo.slug))).scalars().all()
    hit = [r for r in rows if groups & set(r.group_ids or [])]
    return ([{"slug": r.slug, "family": r.family, "base_url": r.base_url, "suite": r.suite, "components": r.components,
              "gpg_key": r.gpg_key} for r in hit], sorted({e for r in hit for e in r.editor_ids or []}))


# ── package operations on one host ───────────────────────────────────────────

class PackageRef(BaseModel):
    name: str = Field(max_length=200)
    arch: str = Field(default="", max_length=20)
    version: str = Field(default="", max_length=200)


class PackageOpIn(BaseModel):
    op: str = Field(pattern="^(plan|refresh|install|upgrade|remove|purge|hold|unhold|pin|repo_apply)$")
    packages: list[PackageRef] = Field(default_factory=list, max_length=500)


def _op_out(o: PackageOp) -> dict:
    return {"id": o.id, "host_id": o.host_id, "op": o.op, "packages": (o.args or {}).get("packages") or [],
            "status": o.status, "job_run_id": o.job_run_id,
            "result": {k: v for k, v in (o.result or {}).items() if k not in ("accountable", "tries")}, "error": o.error,
            "requested_by": o.requested_by, "requested_by_name": o.requested_by_name,
            "created_at": _iso(o.created_at), "finished_at": _iso(o.finished_at)}


@router.post("/hosts/{host_id}/packages", status_code=202)
async def package_op(host_id: str, payload: PackageOpIn, request: Request, caller: Caller = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    inv = await _visible_inventory_host(caller, host_id)
    if clients.os_family(inv.get("os_type")) != "linux" or inv.get("enabled") is False:
        raise _bad("only enabled Linux hosts are supported")
    host = await session.get(PostureHost, host_id)
    busy = (await session.execute(select(RolloutHost.id).where(
        RolloutHost.active_host_id == host_id, RolloutHost.status == "running"))).scalar_one_or_none()
    if busy and payload.op not in ("plan", "refresh"):
        raise _bad("a patch rollout is working on this host", 409)
    full = next((h for h in await clients.inventory_hosts() if h["id"] == host_id), inv)
    repos, editors = await repos_for(session, full, host)
    args: dict = {"repos": repos} if payload.op == "repo_apply" else {
        "packages": [p.model_dump() for p in payload.packages], "repo_mode": "managed" if repos else "host"}
    op = PackageOp(id=str(uuid.uuid4()), host_id=host_id, op=payload.op, args=args, status="queued",
                   result={"accountable": editors},
                   requested_by=caller.user_id, requested_by_name=caller.username)
    await _audit_critical(request, caller, f"posture.package.{payload.op}", "host", host_id,
                          {"packages": [p.name for p in payload.packages][:100], "op_id": op.id})
    try:
        run = await clients.host_op_run(action="package_ops", op=op.op, args=args, host_id=host_id, token=f"op-{op.id}",
                                        requested_by=caller.user_id, approval=None, accountable=editors)
    except clients.UpstreamError as exc:
        raise _bad(exc.detail, exc.status if exc.status in (400, 409) else 502) from exc
    op.job_run_id = run["run_id"]
    op.status = "awaiting_approval" if run.get("status") == "pending_approval" else "queued"
    session.add(op)
    await session.commit()
    return _op_out(op)


class RebootIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/hosts/{host_id}/reboot", status_code=202)
async def reboot_host(host_id: str, payload: RebootIn, request: Request, caller: Caller = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    """Reboot one host now. Tracked as a package operation (op "reboot") so it shows with the
    host's other operations; production hosts follow the same approval rules."""
    inv = await _visible_inventory_host(caller, host_id)
    if clients.os_family(inv.get("os_type")) != "linux" or inv.get("enabled") is False:
        raise _bad("only enabled Linux hosts are supported")
    busy = (await session.execute(select(RolloutHost.id).where(
        RolloutHost.active_host_id == host_id, RolloutHost.status == "running"))).scalar_one_or_none()
    if busy:
        raise _bad("a patch rollout is working on this host", 409)
    queued = (await session.execute(select(PackageOp.id).where(
        PackageOp.host_id == host_id, PackageOp.op == "reboot",
        PackageOp.status.in_(("queued", "running", "awaiting_approval"))))).scalar_one_or_none()
    if queued:
        raise _bad("a reboot of this host is already queued", 409)
    args = {"health_urls": [], "wait_seconds": REBOOT_WAIT_SECONDS}
    op = PackageOp(id=str(uuid.uuid4()), host_id=host_id, op="reboot", args=args, status="queued",
                   result={"accountable": [], "reason": payload.reason.strip()},
                   requested_by=caller.user_id, requested_by_name=caller.username)
    await _audit_critical(request, caller, "posture.host.reboot", "host", host_id,
                          {"op_id": op.id, "reason": payload.reason.strip()[:500]})
    try:
        run = await clients.host_op_run(action=engine.op_action(op.op), op=op.op, args=args, host_id=host_id,
                                        token=f"op-{op.id}", requested_by=caller.user_id, approval=None, accountable=[])
    except clients.UpstreamError as exc:
        raise _bad(exc.detail, exc.status if exc.status in (400, 409) else 502) from exc
    op.job_run_id = run["run_id"]
    op.status = "awaiting_approval" if run.get("status") == "pending_approval" else "queued"
    session.add(op)
    await session.commit()
    return _op_out(op)


@router.get("/package-ops")
async def list_package_ops(host_id: str | None = None, limit: int = Query(default=50, ge=1, le=200),
                           caller: Caller = Depends(get_caller), session: AsyncSession = Depends(get_session)):
    allowed = await clients.visible_host_ids(caller)
    q = select(PackageOp).order_by(PackageOp.created_at.desc()).limit(limit)
    if host_id:
        q = q.where(PackageOp.host_id == host_id)
    if allowed is not None:
        q = q.where(PackageOp.host_id.in_(sorted(allowed) or [""]))
    return [_op_out(o) for o in (await session.execute(q)).scalars().all()]


# ── windows ──────────────────────────────────────────────────────────────────

class WindowIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(default="UTC", max_length=64)
    days: list[int] = Field(min_length=1, max_length=7)
    start: str = Field(max_length=5)
    duration_minutes: int


def _win_dict(w: PatchWindow) -> dict:
    return {"id": w.id, "name": w.name, "timezone": w.timezone, "days": list(w.days or []), "start": w.start,
            "duration_minutes": w.duration_minutes}


def _win_out(w: PatchWindow) -> dict:
    at, d = datetime.now(UTC), _win_dict(w)
    end = core.window_end(d, at)
    return {**d, "open_now": end is not None, "open_until": _iso(end), "next_open": _iso(core.next_open(d, at))}


def _check_win(p: WindowIn) -> dict:
    d = p.model_dump()
    d["days"] = sorted(set(d["days"]))
    try:
        core.check_window(d)
    except core.PlanError as exc:
        raise _bad(str(exc)) from exc
    return d


@router.get("/patch-windows")
async def list_windows(caller: Caller = Depends(get_caller), session: AsyncSession = Depends(get_session)):
    return [_win_out(w) for w in (await session.execute(select(PatchWindow).order_by(PatchWindow.name))).scalars().all()]


@router.post("/patch-windows", status_code=201)
async def create_window(payload: WindowIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                        session: AsyncSession = Depends(get_session)):
    row = PatchWindow(id=str(uuid.uuid4()), **_check_win(payload), editor_ids=[caller.user_id])
    await _audit_critical(request, caller, "posture.patch_window.create", "patch_window", row.id, _win_dict(row))
    session.add(row)
    await _commit_unique(session, "a window with this name")
    return _win_out(row)


@router.put("/patch-windows/{window_id}")
async def update_window(window_id: str, payload: WindowIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                        session: AsyncSession = Depends(get_session)):
    row = await _get(session, PatchWindow, window_id, "window")
    d = _check_win(payload)
    await _audit_critical(request, caller, "posture.patch_window.update", "patch_window", row.id, d)
    for k, v in d.items():
        setattr(row, k, v)
    row.editor_ids = _editors(row.editor_ids, caller.user_id)
    await _commit_unique(session, "a window with this name")
    return _win_out(row)


@router.delete("/patch-windows/{window_id}", status_code=204)
async def delete_window(window_id: str, request: Request, caller: Caller = Depends(require_fleet_admin),
                        session: AsyncSession = Depends(get_session)):
    row = await _get(session, PatchWindow, window_id, "window")
    await _audit_critical(request, caller, "posture.patch_window.delete", "patch_window", row.id, {"name": row.name})
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


# ── baselines ────────────────────────────────────────────────────────────────

class BaselineIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    group_ids: list[str] = Field(default_factory=list, max_length=200)
    config: dict = Field(default_factory=dict)


def _base_out(b: PatchBaseline) -> dict:
    return {"id": b.id, "name": b.name, "description": b.description, "group_ids": list(b.group_ids or []),
            "config": b.config, "updated_at": _iso(b.updated_at)}


def _config(raw: dict) -> dict:
    try:
        return core.normalise_config(raw)
    except core.PlanError as exc:
        raise _bad(str(exc)) from exc


@router.get("/patch-baselines")
async def list_baselines(caller: Caller = Depends(get_caller), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(PatchBaseline).order_by(PatchBaseline.name))).scalars().all()
    return {"items": [_base_out(b) for b in rows], "defaults": core.DEFAULTS}


@router.post("/patch-baselines", status_code=201)
async def create_baseline(payload: BaselineIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                          session: AsyncSession = Depends(get_session)):
    row = PatchBaseline(id=str(uuid.uuid4()), name=payload.name, description=payload.description,
                        group_ids=payload.group_ids, config=_config(payload.config), editor_ids=[caller.user_id])
    await _audit_critical(request, caller, "posture.patch_baseline.create", "patch_baseline", row.id,
                          {"name": row.name, "config": row.config})
    session.add(row)
    await _commit_unique(session, "a baseline with this name")
    return _base_out(row)


@router.put("/patch-baselines/{baseline_id}")
async def update_baseline(baseline_id: str, payload: BaselineIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                          session: AsyncSession = Depends(get_session)):
    row = await _get(session, PatchBaseline, baseline_id, "baseline")
    cfg = _config(payload.config)
    await _audit_critical(request, caller, "posture.patch_baseline.update", "patch_baseline", row.id,
                          {"name": payload.name, "config": cfg})
    row.name, row.description, row.group_ids, row.config = payload.name, payload.description, payload.group_ids, cfg
    row.editor_ids = _editors(row.editor_ids, caller.user_id)
    await _commit_unique(session, "a baseline with this name")
    return _base_out(row)


@router.delete("/patch-baselines/{baseline_id}", status_code=204)
async def delete_baseline(baseline_id: str, request: Request, caller: Caller = Depends(require_fleet_admin),
                          session: AsyncSession = Depends(get_session)):
    row = await _get(session, PatchBaseline, baseline_id, "baseline")
    await _audit_critical(request, caller, "posture.patch_baseline.delete", "patch_baseline", row.id, {"name": row.name})
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


# ── rollouts ─────────────────────────────────────────────────────────────────

class RolloutIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    baseline_id: str | None = Field(default=None, max_length=36)
    config: dict = Field(default_factory=dict)          # overrides on top of the baseline
    host_ids: list[str] = Field(default_factory=list, max_length=MAX_ROLLOUT_HOSTS)
    group_ids: list[str] = Field(default_factory=list, max_length=200)
    finding_ids: list[str] = Field(default_factory=list, max_length=5000)
    window_id: str | None = Field(default=None, max_length=36)
    # Running outside a window is a choice someone makes, never a missing field.
    ignore_window: bool = False


def _rollout_out(r: PatchRollout, counts: dict[str, int] | None = None, allowed: set[str] | None = None) -> dict:
    mine = [h for h in r.plan.get("hosts") or [] if allowed is None or h["host_id"] in allowed]
    return {"id": r.id, "name": r.name, "status": r.status, "status_reason": r.status_reason, "waiting": r.waiting,
            "baseline_id": r.baseline_id, "plan_hash": r.plan_hash, "current_wave": r.current_wave,
            "waves": len(r.plan.get("waves") or []), "hosts": len(mine), "packages": sum(len(h["packages"]) for h in mine),
            "window": r.plan.get("window"), "soak_until": _iso(r.soak_until), "feed_version": r.feed_version,
            "created_by": r.created_by, "created_by_name": r.created_by_name, "approved_by": r.approved_by,
            "approved_by_name": r.approved_by_name, "approved_at": _iso(r.approved_at), "approval": engine.approval_note(r),
            "created_at": _iso(r.created_at),
            "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at), "counts": counts or {}}


def _host_out(h: RolloutHost) -> dict:
    d = h.data or {}
    return {"host_id": h.host_id, "hostname": h.hostname, "wave": h.wave, "status": h.status, "step": h.step,
            "attempt": h.attempt, "error": h.error, "drained": bool(h.drained), "in_maintenance": bool(h.maintenance_id),
            "changed": bool(h.changed), "changes": d.get("changes") or [], "held": d.get("held") or [],
            "reboot": d.get("reboot"), "checks": list(h.checks or []),
            "runs": {k: v.get("run_id") for k, v in (h.runs or {}).items()},
            "started_at": _iso(h.started_at), "finished_at": _iso(h.finished_at)}


async def _hook_ref(template_id: str | None, what: str, accountable: list[str]) -> dict | None:
    if not template_id:
        return None
    try:
        t = await clients.hook_template(template_id)
    except clients.UpstreamError as exc:
        raise _bad(f"{what} template: {exc.detail}") from exc
    # A hook cannot wait for its own approval in the middle of a rollout. With
    # SEYALRUN_ADMIN_APPROVAL=off an admin's rollout (the only kind) needs none.
    if not t["enabled"] or (t["requires_approval"] and admin_approval_required()):
        raise _bad(f"{what} template {t['name']!r} must be enabled and must not need its own approval")
    accountable += t.get("editor_ids") or []
    return {"id": t["id"], "name": t["name"], "updated_at": t["updated_at"]}


async def _build_plan(session: AsyncSession, payload: RolloutIn, caller: Caller) -> tuple[dict, list[str], list[tuple[str, str, str]]]:
    """(plan, accountable ids, reasons hosts were left out)."""
    accountable = [caller.user_id]
    base = None
    if payload.baseline_id:
        base = await _get(session, PatchBaseline, payload.baseline_id, "baseline")
        accountable += base.editor_ids or []
    cfg = _config({**((base.config if base else None) or {}), **payload.config})
    if payload.finding_ids and cfg["scope"] != "findings":
        cfg = _config({**cfg, "scope": "findings"})
    if cfg["scope"] == "findings" and not payload.finding_ids:
        raise _bad("scope 'findings' needs at least one finding")
    window = None
    if payload.window_id:
        win = await _get(session, PatchWindow, payload.window_id, "window")
        window = _win_dict(win)
        accountable += win.editor_ids or []
    elif not payload.ignore_window:
        raise _bad("choose a maintenance window, or set ignore_window to run as soon as it is approved")

    inventory = {h["id"]: h for h in await clients.visible_hosts(caller)}
    groups = set(payload.group_ids or (base.group_ids if base and not payload.host_ids else []) or [])
    wanted = list(dict.fromkeys(payload.host_ids))
    wanted += [hid for hid, h in inventory.items() if groups & set(h.get("group_ids") or []) and hid not in wanted]
    by_host: dict[str, list[PostureFinding]] = {}
    if payload.finding_ids:
        rows = (await session.execute(select(PostureFinding).where(
            PostureFinding.id.in_(payload.finding_ids), PostureFinding.status == "open"))).scalars().all()
        for f in rows:
            by_host.setdefault(f.host_id, []).append(f)
        wanted = [h for h in wanted if h in by_host] if wanted else sorted(by_host)
    unknown = [h for h in wanted if h not in inventory]
    if unknown:
        raise _bad(f"{len(unknown)} host(s) not found", 404)
    if not wanted:
        raise _bad("no hosts selected")
    if len(wanted) > MAX_ROLLOUT_HOSTS:
        raise _bad(f"at most {MAX_ROLLOUT_HOSTS} hosts per rollout")

    clusters = set(cfg["cluster_group_ids"])
    hosts, skipped = [], []
    for hid in wanted:
        inv = inventory[hid]
        name = inv.get("name") or hid
        row = await session.get(PostureHost, hid)
        if clients.os_family(inv.get("os_type")) != "linux" or inv.get("enabled") is False:
            skipped.append((hid, name, "not an enabled Linux host"))
            continue
        if row is None or row.collected_at is None:
            skipped.append((hid, name, "no facts collected yet"))
            continue
        if not facts.updates_known(row.warnings):
            skipped.append((hid, name, "the last scan could not list its updates; rescan the host"))
            continue
        pend = (await session.execute(select(PosturePendingUpdate).where(PosturePendingUpdate.host_id == hid))).scalars().all()
        inst = (await session.execute(select(PostureInstalledPackage).where(PostureInstalledPackage.host_id == hid))).scalars().all()
        if payload.finding_ids:
            found = by_host.get(hid, [])
        else:
            found = (await session.execute(select(PostureFinding).where(
                PostureFinding.host_id == hid, PostureFinding.status == "open"))).scalars().all()
        pend_d = [{"name": p.name, "arch": p.arch, "current": p.current, "available": p.available, "security": p.security} for p in pend]
        inst_d = [{"name": p.name, "arch": p.arch, "source": p.source} for p in inst]
        pkgs = core.select_packages(pend_d, inst_d, [{"package": f.package} for f in found], cfg)
        pkgs = [p for p in pkgs if _NAME.match(p["name"])]
        if not pkgs:
            skipped.append((hid, name, "nothing to patch"))
            continue
        src = {p["name"]: p["source"] or p["name"] for p in inst_d}
        covered = {p["name"] for p in pkgs} | {src.get(p["name"], p["name"]) for p in pkgs}
        repos, editors = await repos_for(session, inv, row)
        accountable += editors
        hosts.append({"host_id": hid, "hostname": row.hostname or name, "family": row.distro_family or row.pkg_manager,
                      "production": bool(inv.get("is_production")), "clusters": sorted(clusters & set(inv.get("group_ids") or [])),
                      "packages": pkgs, "repos": repos,
                      "findings": [{"id": f.id, "cve": f.cve_id, "package": f.package, "installed": f.installed_version,
                                    "fixed": f.fixed_version} for f in found if f.package in covered]})
    if not hosts:
        raise _bad("no host to patch: " + "; ".join(f"{n}: {w}" for _, n, w in skipped[:5]))
    plan = {"config": cfg, "window": window, "hosts": hosts, "waves": core.plan_waves(hosts, cfg),
            "drain_hook": await _hook_ref(cfg["drain_template_id"], "drain", accountable),
            "undrain_hook": await _hook_ref(cfg["undrain_template_id"], "undrain", accountable)}
    return plan, list(dict.fromkeys(accountable)), skipped


@router.post("/patches", status_code=201)
async def create_rollout(payload: RolloutIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                         session: AsyncSession = Depends(get_session)):
    plan, accountable, skipped = await _build_plan(session, payload, caller)
    needs = get_settings().posture_patch_approval != "production" or any(h["production"] for h in plan["hosts"])
    # SEYALRUN_ADMIN_APPROVAL=off: an admin's rollout is approved as it is created,
    # bound to this plan hash. approved_by is the creator, which the approve endpoint
    # never allows, so it reads as "not required" (engine.approval_note), never as
    # an independent approver.
    waived = needs and caller.is_admin and not admin_approval_required()
    if waived:
        needs = False
    snap = await scanning.active_snapshot(session)
    r = PatchRollout(id=str(uuid.uuid4()), name=payload.name, baseline_id=payload.baseline_id, plan=plan,
                     plan_hash=core.plan_hash(plan), status="pending_approval" if needs else "approved", status_reason="",
                     waiting="", created_by=caller.user_id, created_by_name=caller.username, accountable_ids=accountable,
                     approved_by=caller.user_id if waived else None, approved_by_name=caller.username if waived else "",
                     approved_at=engine.now() if waived else None,
                     feed_version=snap.version if snap else "", current_wave=0)
    wave_of = {hid: i for i, w in enumerate(plan["waves"]) for hid in w}
    await _audit_critical(request, caller, "posture.patch.create", "patch_rollout", r.id,
                          {"name": r.name, "hosts": len(plan["hosts"]), "plan_hash": r.plan_hash, "needs_approval": needs,
                           **({"approval": NOT_REQUIRED} if waived else {})})
    session.add(r)
    for h in plan["hosts"]:
        session.add(RolloutHost(id=str(uuid.uuid4()), rollout_id=r.id, host_id=h["host_id"], active_host_id=h["host_id"],
                                hostname=h["hostname"][:255], wave=wave_of[h["host_id"]], status="pending", step="",
                                attempt=1, error="", drained=False, changed=False, runs={}, data={}, checks=[]))
    engine.add_event(session, r.id, "created", by=caller.username)
    if waived:
        engine.add_event(session, r.id, "approved", by=caller.username, detail=f"{NOT_REQUIRED}; plan {r.plan_hash[:12]}")
    for hid, _, why in skipped:     # host-scoped: readers who may not see a host do not see its event
        engine.add_event(session, r.id, "host_left_out", host_id=hid, by=caller.username, detail=why)
    await _commit_unique(session, "a live rollout for one of these hosts")
    if needs:
        # Everyone receives a broadcast, so it says only that there is something to approve.
        await clients.notify(user_id=None, severity="medium", title="A patch rollout is waiting for approval", source_id=r.id)
    return {**_rollout_out(r), "skipped": [f"{n}: {w}" for _, n, w in skipped]}


async def _counts(session: AsyncSession, ids: list[str], allowed: set[str] | None = None) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {i: {} for i in ids}
    if ids:
        q = select(RolloutHost.rollout_id, RolloutHost.status, func.count()).where(RolloutHost.rollout_id.in_(ids))
        if allowed is not None:
            q = q.where(RolloutHost.host_id.in_(sorted(allowed) or [""]))
        for rid, st, n in (await session.execute(q.group_by(RolloutHost.rollout_id, RolloutHost.status))).all():
            out[rid][st] = n
    return out


async def _visible_rollout(caller: Caller, r: PatchRollout) -> set[str] | None:
    """Host ids to show, or None for all. 404 when the caller may see none of them."""
    allowed = await clients.visible_host_ids(caller)
    if allowed is not None and not allowed & {h["host_id"] for h in r.plan["hosts"]}:
        raise _bad("rollout not found", 404)
    return allowed


@router.get("/patches")
async def list_rollouts(status_: str | None = Query(default=None, alias="status", max_length=24),
                        limit: int = Query(default=50, ge=1, le=200), caller: Caller = Depends(get_caller),
                        session: AsyncSession = Depends(get_session)):
    q = select(PatchRollout).order_by(PatchRollout.created_at.desc()).limit(limit)
    if status_:
        q = q.where(PatchRollout.status == status_)
    allowed = await clients.visible_host_ids(caller)
    if allowed is not None:     # before the limit, or a page could come back empty
        q = q.where(PatchRollout.id.in_(select(RolloutHost.rollout_id).where(RolloutHost.host_id.in_(sorted(allowed) or [""]))))
    rows = (await session.execute(q)).scalars().all()
    counts = await _counts(session, [r.id for r in rows], allowed)
    return {"items": [_rollout_out(r, counts[r.id], allowed) for r in rows]}


async def _hosts(session: AsyncSession, rid: str) -> list[RolloutHost]:
    return list((await session.execute(select(RolloutHost).where(RolloutHost.rollout_id == rid)
                                       .order_by(RolloutHost.wave, RolloutHost.hostname))).scalars().all())


@router.get("/patches/{rollout_id}")
async def get_rollout(rollout_id: str, caller: Caller = Depends(get_caller), session: AsyncSession = Depends(get_session)):
    r = await _get(session, PatchRollout, rollout_id, "rollout")
    allowed = await _visible_rollout(caller, r)
    hosts = [h for h in await _hosts(session, r.id) if allowed is None or h.host_id in allowed]
    plan_by = {h["host_id"]: h for h in r.plan["hosts"]}
    events = (await session.execute(select(RolloutEvent).where(RolloutEvent.rollout_id == r.id)
                                    .order_by(RolloutEvent.id.desc()).limit(300))).scalars().all()
    blocked = caller.user_id in set(r.accountable_ids or [])
    return {**_rollout_out(r, (await _counts(session, [r.id], allowed))[r.id], allowed), "config": r.plan["config"],
            "drain_hook": (r.plan.get("drain_hook") or {}).get("name"), "undrain_hook": (r.plan.get("undrain_hook") or {}).get("name"),
            "can_approve": caller.is_admin and not caller.kiosk_host_id and r.status == "pending_approval" and not blocked,
            "approve_blocked_reason": "you created this rollout or edited something it uses" if blocked else "",
            "host_rows": [{**_host_out(h), "production": plan_by[h.host_id]["production"],
                           "packages": plan_by[h.host_id]["packages"], "findings": plan_by[h.host_id]["findings"],
                           "repos": [x["slug"] for x in plan_by[h.host_id]["repos"]]} for h in hosts],
            "events": [{"at": _iso(e.at), "event": e.event, "host_id": e.host_id, "by": e.by, "detail": e.detail}
                       for e in events if allowed is None or e.host_id is None or e.host_id in allowed]}


class ApproveIn(BaseModel):
    plan_hash: str = Field(min_length=64, max_length=64)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/patches/{rollout_id}/approve")
async def approve_rollout(rollout_id: str, payload: ApproveIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                          session: AsyncSession = Depends(get_session)):
    async with engine.lock(rollout_id):
        r = await _get(session, PatchRollout, rollout_id, "rollout")
        if r.status != "pending_approval":
            raise _bad(f"the rollout is {r.status}", 409)
        # F-2: whoever asked for it, or edited the baseline, window or mirror it uses.
        if caller.user_id in {r.created_by, *(r.accountable_ids or [])}:
            raise _bad("a different admin must approve this: you created it or edited something it uses", 403)
        if payload.plan_hash != r.plan_hash or core.plan_hash(r.plan) != r.plan_hash:
            raise _bad("the plan is not the one you reviewed; reload and review it again", 409)
        await _audit_critical(request, caller, "posture.patch.approve", "patch_rollout", r.id,
                              {"plan_hash": r.plan_hash, "requested_by": r.created_by, "hosts": len(r.plan["hosts"])})
        r.status, r.approved_by, r.approved_by_name = "approved", caller.user_id, caller.username
        r.approved_at = engine.now()
        engine.add_event(session, r.id, "approved", by=caller.username, detail=f"plan {r.plan_hash[:12]}")
        await session.commit()
        return _rollout_out(r)


async def _decide(rollout_id: str, request: Request, caller: Caller, session: AsyncSession, action: str,
                  allowed_from: tuple[str, ...], reason: str, new_status: str | None) -> dict:
    async with engine.lock(rollout_id):
        r = await _get(session, PatchRollout, rollout_id, "rollout")
        if r.status not in allowed_from:
            raise _bad(f"the rollout is {r.status}", 409)
        await _audit_critical(request, caller, f"posture.patch.{action}", "patch_rollout", r.id, {"reason": reason, "from": r.status})
        if new_status in ("rejected", "cancelled"):
            await engine.close_rollout(session, r, new_status, f"{caller.username}: {reason}")
        else:
            r.status, r.status_reason, r.waiting = new_status, f"{caller.username}: {reason}"[:2000], ""
            engine.add_event(session, r.id, action, by=caller.username, detail=reason)
            await session.commit()
        return _rollout_out(r)


@router.post("/patches/{rollout_id}/reject")
async def reject_rollout(rollout_id: str, payload: ReasonIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                         session: AsyncSession = Depends(get_session)):
    return await _decide(rollout_id, request, caller, session, "reject", ("pending_approval",), payload.reason, "rejected")


@router.post("/patches/{rollout_id}/pause")
async def pause_rollout(rollout_id: str, payload: ReasonIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                        session: AsyncSession = Depends(get_session)):
    return await _decide(rollout_id, request, caller, session, "pause", ("approved", "running"), payload.reason, "paused")


@router.post("/patches/{rollout_id}/resume")
async def resume_rollout(rollout_id: str, payload: ReasonIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                         session: AsyncSession = Depends(get_session)):
    row = await _get(session, PatchRollout, rollout_id, "rollout")
    limit = row.plan["config"]["max_failures"]
    bad = [h for h in await _hosts(session, rollout_id) if h.status in ("failed", "rolled_back")]
    if len(bad) > limit:
        raise _bad(f"{len(bad)} host(s) are still failed (limit {limit}): retry or skip them first", 409)
    return await _decide(rollout_id, request, caller, session, "resume", ("paused", "halted"), payload.reason,
                         "running" if row.started_at else "approved")


@router.post("/patches/{rollout_id}/cancel")
async def cancel_rollout(rollout_id: str, payload: ReasonIn, request: Request, caller: Caller = Depends(require_fleet_admin),
                         session: AsyncSession = Depends(get_session)):
    return await _decide(rollout_id, request, caller, session, "cancel",
                         ("pending_approval", "approved", "running", "paused", "halted"), payload.reason, "cancelled")


HOST_ACTIONS = {
    "retry": (("failed",), "run this host again from its pre-checks"),
    "skip": (("pending", "failed", "rolled_back"), "leave this host out"),
    "rollback": (("failed", "done"), "put the changed packages back"),
    "release": (("failed", "rolled_back", "skipped", "cancelled"), "undrain and end maintenance"),
    "force_release": (("failed", "rolled_back", "skipped", "cancelled"), "record that a person undrained it by hand"),
}


@router.post("/patches/{rollout_id}/hosts/{host_id}/{action}")
async def host_action(rollout_id: str, host_id: str, action: str, payload: ReasonIn, request: Request,
                      caller: Caller = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if action not in HOST_ACTIONS:
        raise _bad("unknown action", 404)
    async with engine.lock(rollout_id):
        r = await _get(session, PatchRollout, rollout_id, "rollout")
        allowed = await _visible_rollout(caller, r)
        rh = (await session.execute(select(RolloutHost).where(
            RolloutHost.rollout_id == rollout_id, RolloutHost.host_id == host_id))).scalar_one_or_none()
        if rh is None or (allowed is not None and host_id not in allowed):
            raise _bad("host is not in this rollout", 404)
        async with engine.lock(rh.id):
            await session.refresh(rh)
            if rh.status not in HOST_ACTIONS[action][0]:
                raise _bad(f"cannot {action} a host that is {rh.status}", 409)
            # Both change the host under the rollout's approval, which ends with the rollout.
            if action in ("retry", "rollback") and r.status not in ("running", "paused", "halted"):
                raise _bad(f"the rollout is {r.status}; its approval no longer covers changes to a host", 409)
            if action == "rollback" and not any(c["old"] and c["new"] for c in (rh.data or {}).get("changes") or []):
                raise _bad("there is no recorded package change to roll back", 409)
            if action in ("rollback", "release") and not r.started_at:
                raise _bad("the rollout never started", 409)
            await _audit_critical(request, caller, f"posture.patch.host_{action}", "patch_rollout", r.id,
                                  {"host_id": host_id, "reason": payload.reason, "from": rh.status})
            engine.add_event(session, r.id, f"host_{action}_requested", host_id=host_id, by=caller.username, detail=payload.reason)
            data, was = dict(rh.data or {}), rh.status
            if action == "force_release":
                rh.drained, rh.maintenance_id = False, None
                if was != "failed" or r.status not in engine.LIVE_ROLLOUT:
                    rh.active_host_id = None
            elif action == "skip":
                if rh.drained or rh.maintenance_id:
                    raise _bad("release this host first: it is still drained or in maintenance", 409)
                rh.status, rh.active_host_id, rh.finished_at = "skipped", None, engine.now()
            else:
                rh.active_host_id = host_id
                rh.attempt += 1
                failed_with = rh.error
                rh.status, rh.error, rh.finished_at = "running", "", None
                if action == "retry":
                    # Back in the queue, so the window, pause and concurrency apply. What an earlier
                    # attempt changed, and the state before it, stay: checks, reboot and rollback
                    # are still about the whole change.
                    prior = list(data.get("changes") or [])
                    keep = {"prior_changes": prior, "changes": prior, "maintenance_until": data.get("maintenance_until")}
                    if prior or (data.get("reboot") or {}).get("needed"):
                        keep["snapshot_first"] = data.get("snapshot_first") or data.get("snapshot_before")
                    rh.runs, rh.data = {}, keep
                    rh.status, rh.step, rh.wave = "pending", "", min(rh.wave, r.current_wave)
                elif action == "rollback":
                    data.pop("final_status", None)
                    data.pop("final_error", None)
                    rh.data = data
                    rh.runs = {k: v for k, v in (rh.runs or {}).items() if k != "rollback"}
                    engine.go(rh, "rollback")
                else:
                    data["final_status"] = was
                    data.setdefault("final_error", failed_with)
                    rh.data = data
                    rh.runs = {k: v for k, v in (rh.runs or {}).items() if k != "undrain"}
                    engine.go(rh, "undrain")
            await _commit_unique(session, "another live rollout for this host")
            return _host_out(rh)


@router.get("/patches/{rollout_id}/evidence")
async def evidence(rollout_id: str, format: str = Query(default="json", pattern="^(json|csv)$"),
                   caller: Caller = Depends(get_caller), session: AsyncSession = Depends(get_session)):
    r = await _get(session, PatchRollout, rollout_id, "rollout")
    allowed = await _visible_rollout(caller, r)
    hosts = [h for h in await _hosts(session, r.id) if allowed is None or h.host_id in allowed]
    plan_by = {h["host_id"]: h for h in r.plan["hosts"]}
    ids = [f["id"] for h in hosts for f in plan_by[h.host_id]["findings"]]
    state: dict[str, PostureFinding] = {}
    for i in range(0, len(ids), 500):
        for f in (await session.execute(select(PostureFinding).where(PostureFinding.id.in_(ids[i:i + 500])))).scalars().all():
            state[f.id] = f
    rows = []
    for h in hosts:
        ch = {c["name"]: c for c in (h.data or {}).get("changes") or []}
        for p in plan_by[h.host_id]["packages"]:
            c = ch.get(p["name"])
            cves = [f for f in plan_by[h.host_id]["findings"] if f["package"] == p["name"]]
            for f in cves or [None]:
                cur = state.get(f["id"]) if f else None
                rows.append({"host": h.hostname, "host_status": h.status, "package": p["name"], "planned": p["target"],
                             "before": c["old"] if c else p["current"], "after": (c["old"] if h.status == "rolled_back" else c["new"]) if c else "",
                             "cve": f["cve"] if f else "", "finding_status": cur.status if cur else "",
                             "fixed_at": _iso(cur.fixed_at) if cur else None,
                             "patch_run": (h.runs or {}).get("patch", {}).get("run_id") or "",
                             "reboot_run": (h.runs or {}).get("reboot", {}).get("run_id") or ""})
    head = {"rollout": r.name, "rollout_id": r.id, "status": r.status, "plan_hash": r.plan_hash, "feed_version": r.feed_version,
            "requested_by": r.created_by_name, "approved_by": r.approved_by_name, "approved_at": _iso(r.approved_at),
            "approval": engine.approval_note(r), "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at)}
    if format == "csv":
        buf = io.StringIO()
        cols = ["rollout", "requested_by", "approved_by", "approved_at", "approval", "plan_hash", "feed_version",
                *(rows[0] if rows else [])]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            # A leading = + - @ would run as a formula in a spreadsheet.
            w.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t") else v)
                        for k, v in {**head, **row}.items()})
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="patch-evidence-{r.id[:8]}.csv"'})
    return {**head, "rows": rows, "hosts": [{"host": h.hostname, "status": h.status, "error": h.error,
                                             "checks": list(h.checks or []), "reboot": (h.data or {}).get("reboot")} for h in hosts]}
