"""Drives patch rollouts (3.0 M4). Every decision is in rollout_core.

All state is in the database, so a restart of this service or of automation-service
resumes a rollout where it was. A step is repeated only by asking automation-service
for an operation with the SAME token, which the host itself refuses to run twice.

Per host:  repos → precheck → maintenance → drain → patch → facts → reboot →
           postcheck → rescan → undrain → release → finish
A failure stops that host where it is. A host that was already changed stays drained
and in maintenance until a person releases it; the rollout halts once more hosts have
failed than the plan allows. Hosts already in progress are always allowed to finish:
interrupting a package transaction is worse than completing it.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from . import audit, clients, collector, rollout_core as core
from .config import get_settings
from .database import SessionLocal
from .models import (
    PackageOp,
    PatchRollout,
    PostureFinding,
    PostureHost,
    PostureInstalledPackage,
    PostureScan,
    RolloutEvent,
    RolloutHost,
)

logger = logging.getLogger(__name__)

NEXT, WAIT = "next", "wait"
STEPS = ("repos", "precheck", "maintenance", "drain", "patch", "facts", "reboot", "postcheck", "rescan",
         "undrain", "release", "finish")
LIVE_ROLLOUT = ("approved", "running", "paused", "halted")
_DONE = {"success", "failed", "error", "cancelled", "rejected", "timeout"}
_BUSY_RETRY = {0, 409, 500, 502, 503, 504}
MAX_TRIES = 10
RELAUNCH_AFTER = timedelta(seconds=60)
SCAN_WAIT = timedelta(minutes=10)
MAINTENANCE_MINUTES = 360

# Single-process service (like scanning.WRITE_LOCK). Rollout rows are written under the
# rollout's lock, host rows under the host's, by the engine and the API alike.
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def lock(key: str) -> asyncio.Lock:
    return _locks[key]


# Fewer than the database pool: one connection per host being advanced.
_slots = asyncio.Semaphore(8)


class StepFailed(RuntimeError):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def add_event(session, rollout_id: str, event: str, *, host_id: str | None = None, by: str = "", detail: str = "") -> None:
    session.add(RolloutEvent(rollout_id=rollout_id, host_id=host_id, event=event, by=by[:200], detail=detail[:4000]))


def approval_of(r: PatchRollout) -> dict | None:
    if not r.approved_by:
        return None
    return {"reference": f"rollout:{r.id}", "approved_by": r.approved_by,
            "accountable_user_ids": list(r.accountable_ids or [])}


def host_plan(r: PatchRollout, host_id: str) -> dict:
    return next(h for h in r.plan["hosts"] if h["host_id"] == host_id)


def _token(rh: RolloutHost, key: str) -> str:
    return f"{rh.id}-{rh.attempt}-{key}"


def _set(rh: RolloutHost, field: str, **kv) -> None:
    setattr(rh, field, {**(getattr(rh, field) or {}), **kv})     # new object: JSON change is tracked


def add_checks(rh: RolloutHost, phase: str, checks: list[dict]) -> None:
    rh.checks = [*(rh.checks or []), *({"phase": phase, **c} for c in checks)]


# ── jobs ─────────────────────────────────────────────────────────────────────

async def _job(session, rh: RolloutHost, key: str, start, *, once: bool = False) -> dict | None:
    """Start the run for ``key`` once, then poll it. Returns the finished run.
    ``once``: the work has no token on the host, so it is never started a second time
    after it may already have run."""
    rec = (rh.runs or {}).get(key)

    async def launch(tries: int) -> None:
        if once:    # recorded before it can start: a crash here must not start it again
            _set(rh, "runs", **{key: {"run_id": None, "tries": tries, "at": now().isoformat()}})
        await session.commit()      # also gives the connection back while waiting on HTTP
        try:
            run = await start()
        except clients.UpstreamError as exc:
            if exc.status in _BUSY_RETRY and not (once and exc.status in (0, 409, 504)):
                _set(rh, "data", last_wait=exc.detail[:300])
                if once:
                    rh.runs = {k: v for k, v in rh.runs.items() if k != key}
                return
            raise StepFailed(exc.detail) from exc
        _set(rh, "runs", **{key: {"run_id": run["run_id"], "tries": tries, "at": now().isoformat()}})

    if rec is None:
        await launch(1)
        return None
    if not rec["run_id"]:
        raise StepFailed(f"the service stopped while starting the {key} run; check whether it ran on the host")
    await session.commit()
    try:
        run = await clients.job_run(rec["run_id"])
    except httpx.HTTPError:
        return None
    if run is None:
        raise StepFailed("the job run no longer exists in automation-service")
    status = run.get("status")
    if status in ("cancelled", "rejected"):
        raise StepFailed(f"the {key} run was {status}; check the host before retrying")
    # "error" is the follower dying (job time limit, crash), not the operation failing:
    # the host carries on, so follow it again with the same token.
    lost = status in ("error", "timeout") and not once
    if status in _DONE and not lost:
        return run
    if lost or status != "pending_approval" and not run.get("live") and now() - datetime.fromisoformat(rec["at"]) > RELAUNCH_AFTER:
        if once:
            raise StepFailed(f"the {key} run was interrupted; check the host before retrying")
        # automation-service restarted under it. Same token: the host re-attaches.
        if rec["tries"] >= MAX_TRIES:
            raise StepFailed(f"the {key} run stopped responding")
        await launch(rec["tries"] + 1)
    return None


async def _host_job(session, r: PatchRollout, rh: RolloutHost, key: str, action: str, op: str, args: dict) -> dict | None:
    return await _job(session, rh, key, lambda: clients.host_op_run(
        action=action, op=op, args=args, host_id=rh.host_id, token=_token(rh, key),
        requested_by=r.created_by, approval=approval_of(r), accountable=list(r.accountable_ids or [])))


def _tail(run: dict, n: int = 3) -> str:
    lines = [str(x).split("] ", 1)[-1] for x in (run.get("output_lines") or [])]
    lines = [x for x in lines if x and not x.startswith("SEYALRUN-") and len(x) < 300 and " " in x]
    return " / ".join(lines[-n:])[:600] or f"exit code {run.get('exit_code')}"


# ── steps ────────────────────────────────────────────────────────────────────

async def step_repos(session, r, rh):
    # Also with no mirrors: files of a mirror that was removed must not stay in use.
    repos = host_plan(r, rh.host_id).get("repos") or []
    run = await _host_job(session, r, rh, "repos", "package_ops", "repo_apply", {"repos": repos})
    if run is None:
        return WAIT
    if run["status"] != "success":
        raise StepFailed("mirror repos could not be applied: " + _tail(run))
    return NEXT


async def _snapshot(session, r, rh, key: str) -> dict | None:
    run = await _host_job(session, r, rh, key, "host_reboot", "snapshot", {"health_urls": r.plan["config"]["health_urls"]})
    if run is None:
        return None
    if run["status"] != "success":
        raise StepFailed("the host state could not be read: " + _tail(run))
    try:
        return core.parse_snapshot(run.get("output_lines") or [])
    except core.PayloadError as exc:
        raise StepFailed(str(exc)) from exc


async def _problems(session, r, rh) -> list[dict] | None:
    """Open Zabbix problems, or None when Zabbix is not usable for this host."""
    cfg = r.plan["config"]
    if cfg["zabbix"] == "off":
        return None
    await session.commit()
    try:
        res = await clients.zabbix("GET", "/problems", params={"host_id": rh.host_id, "min_severity": cfg["zabbix_min_severity"]})
        return res["problems"]
    except clients.UpstreamError as exc:
        if cfg["zabbix"] == "required":
            raise StepFailed(f"Zabbix is required and unavailable: {exc.detail}") from exc
        _set(rh, "data", zabbix_note=exc.detail[:300])
        return None


async def step_precheck(session, r, rh):
    snap = await _snapshot(session, r, rh, "precheck")
    if snap is None:
        return WAIT
    checks = core.precheck(snap, r.plan["config"])
    problems = await _problems(session, r, rh)
    if problems is None:
        checks.append(core.check("no open Zabbix problems", None, (rh.data or {}).get("zabbix_note") or "Zabbix checks are off"))
    else:
        checks.append(core.check("no open Zabbix problems", not problems, "; ".join(p["name"] for p in problems[:5])))
    # Cluster peers: never take a second member while one is not healthy.
    mine = set(host_plan(r, rh.host_id)["clusters"])
    if mine:
        peers = [h["host_id"] for h in r.plan["hosts"] if h["host_id"] != rh.host_id and mine & set(h["clusters"])]
        rows = (await session.execute(select(RolloutHost).where(
            RolloutHost.rollout_id == r.id, RolloutHost.host_id.in_(peers or [""])))).scalars().all()
        bad = sum(1 for p in rows if p.status in ("running", "failed") or p.drained)
        checks.append(core.check("cluster peers healthy", not bad, f"{bad} of {len(peers)} peers running, failed or drained"))
    add_checks(rh, "pre", checks)
    # A retry after the host was changed compares against the state before the FIRST
    # attempt: judged against the broken state, anything would look fine.
    _set(rh, "data", problems_before=[p["trigger_id"] for p in problems or []],
         snapshot_before=(rh.data or {}).get("snapshot_first") or snap)
    bad = core.failed(checks)
    if bad:
        raise StepFailed("pre-check failed: " + "; ".join(f"{c['name']} ({c['detail']})" for c in bad))
    return NEXT


async def _open_maintenance(rh: RolloutHost) -> None:
    """One maintenance per host in a rollout (retries reuse it), extended while needed."""
    res = await clients.zabbix("POST", "/maintenance", json_body={
        "host_id": rh.host_id, "reference": rh.id, "minutes": MAINTENANCE_MINUTES,
        "description": "Opened by a SeyalRun patch rollout"})
    rh.maintenance_id = str(res["maintenance_id"])
    _set(rh, "data", maintenance_until=(now() + timedelta(minutes=MAINTENANCE_MINUTES)).isoformat())


async def extend_maintenance() -> None:
    """A host that is still being worked on, or waits for a person after a failure, must
    not drift out of maintenance."""
    async with SessionLocal() as session:
        ids = (await session.execute(select(RolloutHost.id).where(RolloutHost.maintenance_id.is_not(None)))).scalars().all()
    for rid in ids:
        async with lock(rid), SessionLocal() as session:
            rh = await session.get(RolloutHost, rid)
            until = (rh.data or {}).get("maintenance_until") if rh and rh.maintenance_id else None
            if not until or datetime.fromisoformat(until) - now() > timedelta(minutes=MAINTENANCE_MINUTES // 2):
                continue
            await session.commit()
            try:
                await _open_maintenance(rh)
            except clients.UpstreamError as exc:
                logger.error("maintenance of rollout host %s not extended: %s", rid, exc.detail)
                _set(rh, "data", maintenance_until=(now() + timedelta(minutes=MAINTENANCE_MINUTES // 2 + 5)).isoformat())
            await session.commit()


async def step_maintenance(session, r, rh):
    cfg = r.plan["config"]
    if cfg["zabbix"] == "off":
        return NEXT
    await session.commit()
    try:
        await _open_maintenance(rh)
        add_checks(rh, "pre", [core.check("Zabbix maintenance", True, f"maintenance {rh.maintenance_id}")])
    except clients.UpstreamError as exc:
        if cfg["zabbix"] == "required":
            raise StepFailed(f"Zabbix maintenance could not be opened: {exc.detail}") from exc
        add_checks(rh, "pre", [core.check("Zabbix maintenance", None, exc.detail)])
    return NEXT


async def _hook(session, r, rh, which: str):
    hook = r.plan.get(f"{which}_hook")
    if not hook:
        return NEXT
    run = await _job(session, rh, which, lambda: clients.hook_run(
        template_id=hook["id"], expect_updated_at=hook["updated_at"], host_id=rh.host_id,
        requested_by=r.created_by, approval=approval_of(r), accountable=list(r.accountable_ids or [])), once=True)
    if run is None:
        return WAIT
    if run["status"] != "success":
        raise StepFailed(f"the {which} hook failed: " + _tail(run))
    return NEXT


async def step_drain(session, r, rh):
    if not r.plan.get("drain_hook"):
        return NEXT
    rh.drained = True       # from the moment the hook may have started
    return await _hook(session, r, rh, "drain")


async def step_patch(session, r, rh):
    plan = host_plan(r, rh.host_id)
    exact = r.plan["config"]["versions"] == "exact"
    pkgs = [{"name": p["name"], "arch": p["arch"] if p["arch"] not in ("", "all", "noarch") else "",
             "version": p["target"] if exact else ""} for p in plan["packages"]]
    if not pkgs:
        return NEXT
    if "patch" not in (rh.runs or {}):
        rh.changed = True
    mode = "managed" if plan.get("repos") else "host"
    run = await _host_job(session, r, rh, "patch", "package_ops", "upgrade", {"packages": pkgs, "repo_mode": mode})
    if run is None:
        return WAIT
    try:
        ch = core.parse_changes(run.get("output_lines") or [])
        _set(rh, "data", changes=core.merge_changes((rh.data or {}).get("prior_changes") or [], core.pair_changes(ch)), held=sorted(set(ch["held"])))
        rh.changed = bool(rh.data["changes"])
    except core.PayloadError as exc:
        if run["status"] == "success":
            raise StepFailed(f"the upgrade finished but {exc}") from exc
    if run["status"] != "success" and run.get("exit_code") != 65:      # 65: every package is held
        raise StepFailed("the upgrade failed: " + _tail(run))
    return NEXT


async def _collect(session, r, rh, key: str) -> PostureHost | None:
    """Facts collected AFTER this call started. None while waiting."""
    rec = (rh.runs or {}).get(key)
    if rec is None:
        await session.commit()
        try:
            runs = await collector.request_collection([{"id": rh.host_id, "name": rh.hostname, "os_type": "linux"}],
                                                      r.created_by, approval_of(r))
        except Exception as exc:        # HTTPException from clients: retry until the step deadline
            _set(rh, "data", last_wait=str(getattr(exc, "detail", exc))[:300])
            return None
        # A run that was already live started before the change: wait it out and ask again.
        if runs and not runs[0].get("reused"):
            _set(rh, "runs", **{key: {"run_id": runs[0]["run_id"], "tries": 1, "at": now().isoformat()}})
        return None
    row = await session.get(PostureHost, rh.host_id, populate_existing=True)
    if row is None or row.collect_state in collector.IN_FLIGHT:
        return None
    if row.last_result == "failed":
        retries = (rh.data or {}).get(f"{key}_retries", 0) + 1
        if retries < 3:
            _set(rh, "data", **{f"{key}_retries": retries})
            rh.runs = {k: v for k, v in rh.runs.items() if k != key}
            return None
        raise StepFailed(f"host facts could not be collected: {row.last_error or 'unknown error'}")
    collected = aware(row.collected_at)
    if collected is None or collected < datetime.fromisoformat(rec["at"]):
        return None
    return row


async def step_facts(session, r, rh):
    if not (rh.data or {}).get("changes"):
        return NEXT
    row = await _collect(session, r, rh, "facts")
    if row is None:
        return WAIT
    need, why = core.reboot_needed(r.plan["config"]["reboot"], row.reboot_required, rh.data["changes"])
    _set(rh, "data", reboot={"needed": need, "why": why})
    return NEXT


async def step_reboot(session, r, rh):
    cfg = r.plan["config"]
    decision = (rh.data or {}).get("reboot")
    if decision is None:
        decision = {"needed": cfg["reboot"] == "always", "why": f"policy: {cfg['reboot']}; nothing changed"}
        _set(rh, "data", reboot=decision)
    if not decision["needed"]:
        return NEXT
    rh.changed = True
    run = await _host_job(session, r, rh, "reboot", "host_reboot", "reboot",
                          {"health_urls": cfg["health_urls"], "wait_seconds": cfg["reboot_wait_seconds"]})
    if run is None:
        return WAIT
    if run["status"] != "success":
        raise StepFailed("the reboot failed: " + _tail(run))
    return NEXT


async def step_postcheck(session, r, rh):
    data = rh.data or {}
    if not data.get("changes") and not (data.get("reboot") or {}).get("needed"):
        return NEXT
    tries = data.get("post_tries", 1)
    snap = await _snapshot(session, r, rh, f"postcheck{tries}")
    if snap is None:
        return WAIT
    rebooted = bool((data.get("reboot") or {}).get("needed"))
    checks = core.postcheck(data["snapshot_before"], snap, r.plan["config"], rebooted=rebooted)
    if core.failed(checks) and tries < 4:
        # Services need a moment after boot; judge the state after it settles.
        if not data.get("post_wait"):
            _set(rh, "data", post_wait=(now() + timedelta(seconds=45)).isoformat())
            return WAIT
        if now() < datetime.fromisoformat(data["post_wait"]):
            return WAIT
        _set(rh, "data", post_tries=tries + 1, post_wait=None)
        return WAIT
    add_checks(rh, "post", checks)
    _set(rh, "data", snapshot_after=snap)
    bad = core.failed(checks)
    if bad:
        raise StepFailed("post-check failed: " + "; ".join(f"{c['name']} ({c['detail']})" for c in bad))
    return NEXT


async def step_rescan(session, r, rh):
    data = rh.data or {}
    plan = host_plan(r, rh.host_id)
    row = await _collect(session, r, rh, "rescan")
    if row is None:
        return WAIT
    # The native match is queued by the ingest that follows every collection.
    since = datetime.fromisoformat(rh.runs["rescan"]["at"])
    scan = (await session.execute(select(PostureScan).where(
        PostureScan.scanner == "native", PostureScan.host_id == rh.host_id, PostureScan.started_at >= since)
        .order_by(PostureScan.started_at.desc()).limit(1))).scalar_one_or_none()
    if (scan is None or scan.status == "running") and now() - since < SCAN_WAIT:
        return WAIT
    installed = (await session.execute(select(PostureInstalledPackage).where(
        PostureInstalledPackage.host_id == rh.host_id))).scalars().all()
    scheme = "deb" if row.pkg_manager == "dpkg" else "rpm"
    checks = core.verify_packages(plan["packages"], [{"name": p.name, "arch": p.arch, "version": p.version} for p in installed],
                                  scheme, data.get("held") or [])
    ids = [f["id"] for f in plan["findings"]]
    if ids:
        rows = (await session.execute(select(PostureFinding).where(PostureFinding.id.in_(ids)))).scalars().all()
        still = sorted({f.cve_id for f in rows if f.status == "open"})
        if scan is None or scan.status != "ok":
            checks.append(core.check("findings closed", None, (scan.error if scan else None) or "the vulnerability match did not run"))
        else:
            # Reported, not failing: a feed can list a fix the mirror does not carry yet.
            checks.append(core.check("findings closed", True if not still else None,
                                     f"{len(ids) - len(still)} of {len(ids)} closed" + (f"; open: {', '.join(still[:8])}" if still else "")))
    add_checks(rh, "verify", checks)
    bad = core.failed(checks)
    if bad:
        raise StepFailed("verification failed: " + "; ".join(f"{c['name']} ({c['detail']})" for c in bad[:10]))
    return NEXT


async def step_undrain(session, r, rh):
    if not rh.drained:
        return NEXT
    res = await _hook(session, r, rh, "undrain")
    if res == NEXT:
        rh.drained = False
    return res


async def step_release(session, r, rh):
    if rh.maintenance_id:
        await session.commit()
        try:
            await clients.zabbix("DELETE", f"/maintenance/{rh.maintenance_id}")
        except clients.UpstreamError as exc:
            if exc.status in _BUSY_RETRY:
                return WAIT
            add_event(session, r.id, "maintenance_not_closed", host_id=rh.host_id, detail=exc.detail)
        rh.maintenance_id = None
    return NEXT


async def step_rollback(session, r, rh):
    changes = [c for c in (rh.data or {}).get("changes") or [] if c["old"] and c["new"]]
    if not changes:
        raise StepFailed("there is no recorded package change to roll back")
    mode = "managed" if host_plan(r, rh.host_id).get("repos") else "host"
    run = await _host_job(session, r, rh, "rollback", "package_ops", "rollback", {"repo_mode": mode, "packages": [
        {"name": c["name"], "arch": c["arch"] if c["arch"] not in ("all", "noarch") else "", "version": c["old"]} for c in changes]})
    if run is None:
        return WAIT
    if run["status"] != "success":
        raise StepFailed("the rollback failed: " + _tail(run))
    add_checks(rh, "rollback", [core.check("packages rolled back", True, f"{len(changes)} packages (kernels stay installed)")])
    return NEXT


_HANDLERS = {"repos": step_repos, "precheck": step_precheck, "maintenance": step_maintenance, "drain": step_drain,
             "patch": step_patch, "facts": step_facts, "reboot": step_reboot, "postcheck": step_postcheck,
             "rescan": step_rescan, "undrain": step_undrain, "release": step_release, "rollback": step_rollback}


def go(rh: RolloutHost, step: str) -> None:
    rh.step, rh.step_started_at = step, now()


def finish_host(session, r: PatchRollout, rh: RolloutHost, status: str, error: str = "") -> None:
    rh.status, rh.finished_at = status, now()
    if error:
        rh.error = error[:4000]
    # A failed host keeps its claim: it may still be drained, half patched or retried.
    # Once the rollout is over, only a host still drained or in maintenance does.
    held = rh.drained or bool(rh.maintenance_id)
    if not (held or (status == "failed" and r.status in LIVE_ROLLOUT)):
        rh.active_host_id = None
    add_event(session, r.id, f"host_{status}", host_id=rh.host_id, detail=error)


async def advance_host(rh_id: str) -> None:
    """Advance one host until it has to wait."""
    async with _slots, lock(rh_id), SessionLocal() as session:
        rh = await session.get(RolloutHost, rh_id)
        if rh is None or rh.status != "running":
            return
        r = await session.get(PatchRollout, rh.rollout_id)
        for _ in range(len(STEPS) + 2):
            if rh.step == "finish":
                final = (rh.data or {}).get("final_status") or "done"
                # Released is not repaired: the evidence keeps why the host failed.
                finish_host(session, r, rh, final, (rh.data or {}).get("final_error") or "" if final == "failed" else "")
                await session.commit()
                return
            try:
                started = aware(rh.step_started_at) or now()
                if now() - started > timedelta(minutes=get_settings().posture_step_timeout_minutes):
                    wait = (rh.data or {}).get("last_wait")
                    raise StepFailed(f"step '{rh.step}' did not finish in time" + (f" ({wait})" if wait else ""))
                result = await _HANDLERS[rh.step](session, r, rh)
            except StepFailed as exc:
                await _host_failed(session, r, rh, str(exc))
                await session.commit()
                return
            await session.commit()
            if result == WAIT:
                return
            if rh.step == "rollback":
                _set(rh, "data", rolled_back=True)
                finish_host(session, r, rh, "rolled_back", rh.error)
                await session.commit()
                return
            go(rh, STEPS[STEPS.index(rh.step) + 1])
            await session.commit()


async def _host_failed(session, r: PatchRollout, rh: RolloutHost, reason: str) -> None:
    step = rh.step
    if (rh.data or {}).get("final_status"):      # an operator's release or a rollback of a finished host
        finish_host(session, r, rh, "failed", f"{step}: {reason}")
        return
    if r.plan["config"]["rollback_on_failure"] and step in ("patch", "facts", "postcheck", "rescan") \
            and any(c["old"] and c["new"] for c in (rh.data or {}).get("changes") or []):
        rh.error = f"{step}: {reason}"[:4000]
        add_event(session, r.id, "rollback_started", host_id=rh.host_id, detail=reason)
        go(rh, "rollback")
        return
    finish_host(session, r, rh, "failed", f"{rh.error + '; ' if step == 'rollback' and rh.error else ''}{step}: {reason}")


# ── rollouts ─────────────────────────────────────────────────────────────────

async def halt(session, r: PatchRollout, reason: str) -> None:
    """``reason`` names no host: readers who see only some hosts read it too. What
    happened on a host is in that host's own row and events."""
    r.status, r.status_reason, r.waiting = "halted", reason[:2000], ""
    add_event(session, r.id, "halted", detail=reason)
    await session.commit()
    await audit.log_action(user_id=None, username="posture-service", action="posture.patch.halt",
                           resource_type="patch_rollout", resource_id=r.id, details={"reason": reason[:500]}, result="failure")
    for uid in dict.fromkeys(u for u in (r.created_by, r.approved_by) if u):
        await clients.notify(user_id=uid, severity="critical", title=f"Patch rollout halted: {r.name}", message=reason, source_id=r.id)


async def close_rollout(session, r: PatchRollout, status: str, reason: str = "") -> None:
    """Terminal. Hosts that are still drained or in maintenance keep their claim."""
    r.status, r.status_reason, r.finished_at, r.waiting = status, reason[:2000], now(), ""
    for rh in (await session.execute(select(RolloutHost).where(RolloutHost.rollout_id == r.id))).scalars().all():
        if rh.status == "pending":
            finish_host(session, r, rh, "cancelled")
        elif rh.status == "failed" and not rh.drained and not rh.maintenance_id:
            rh.active_host_id = None
    add_event(session, r.id, status, detail=reason)
    await session.commit()


async def _soak_problem(session, r: PatchRollout, hosts: list[RolloutHost]) -> str:
    """A Zabbix problem that was not there before patching, on this wave's hosts."""
    if r.plan["config"]["zabbix"] == "off":
        return ""
    for rh in hosts:
        if rh.status != "done":
            continue
        try:
            found = await _problems(session, r, rh)
        except StepFailed as exc:
            return str(exc)
        before = set((rh.data or {}).get("problems_before") or [])
        new = [p["name"] for p in found or [] if p["trigger_id"] not in before]
        if new:
            add_event(session, r.id, "new_zabbix_problem", host_id=rh.host_id, detail="; ".join(new[:5]))
            return "a new Zabbix problem appeared on a patched host during the soak"
    return ""


async def advance_rollout(rollout_id: str) -> None:
    async with SessionLocal() as session:
        running = (await session.execute(select(RolloutHost.id).where(
            RolloutHost.rollout_id == rollout_id, RolloutHost.status == "running"))).scalars().all()
    for res in await asyncio.gather(*(advance_host(i) for i in running), return_exceptions=True):
        if isinstance(res, Exception):
            logger.error("rollout %s: host step raised", rollout_id, exc_info=res)
    async with lock(rollout_id), SessionLocal() as session:
        r = await session.get(PatchRollout, rollout_id)
        if r is None or r.status not in ("approved", "running"):
            return
        cfg, window, at = r.plan["config"], r.plan.get("window"), now()
        is_open = window is None or core.window_end(window, at) is not None
        if r.status == "approved":
            if not is_open:
                nxt = core.next_open(window, at)
                r.waiting = f"waiting for window {window['name']}" + (f" (opens {nxt:%Y-%m-%d %H:%M} UTC)" if nxt else "")
                await session.commit()
                return
            r.status, r.started_at, r.waiting = "running", at, ""
            add_event(session, r.id, "started")
            await session.commit()
        hosts = (await session.execute(select(RolloutHost).where(RolloutHost.rollout_id == r.id)
                                       .order_by(RolloutHost.wave, RolloutHost.host_id))).scalars().all()
        bad = [h for h in hosts if h.status in ("failed", "rolled_back")]
        if len(bad) > cfg["max_failures"]:
            await halt(session, r, f"{len(bad)} host(s) failed (limit {cfg['max_failures']})")
            return
        wave = [h for h in hosts if h.wave == r.current_wave]
        # A retried host of an earlier wave goes again too.
        pending = [h for h in hosts if h.wave <= r.current_wave and h.status == "pending"]
        live = [h for h in hosts if h.status == "running"]
        if pending:
            if not is_open:
                nxt = core.next_open(window, at)
                r.waiting = f"window {window['name']} is closed; {len(pending)} host(s) of wave {r.current_wave + 1} wait" \
                    + (f" until {nxt:%Y-%m-%d %H:%M} UTC" if nxt else "")
                await session.commit()
                return
            r.waiting = ""
            for rh in pending[:max(0, cfg["concurrency"] - len(live))]:
                rh.status, rh.started_at = "running", at
                go(rh, STEPS[0])
                add_event(session, r.id, "host_started", host_id=rh.host_id)
            await session.commit()
            return
        if live:
            return
        last = r.current_wave >= max(h.wave for h in hosts)
        if r.soak_until is None:
            if any(h.status == "done" for h in wave) and cfg["soak_minutes"]:
                r.soak_until = at + timedelta(minutes=cfg["soak_minutes"])
                r.waiting = f"soaking wave {r.current_wave + 1} for {cfg['soak_minutes']} min"
                add_event(session, r.id, "wave_soaking", detail=f"wave {r.current_wave + 1}")
                await session.commit()
                return
        elif at < aware(r.soak_until):
            return
        problem = await _soak_problem(session, r, wave)
        if problem:
            await halt(session, r, problem)
            return
        add_event(session, r.id, "wave_done", detail=f"wave {r.current_wave + 1}")
        r.soak_until, r.waiting = None, ""
        if not last:
            r.current_wave += 1
            await session.commit()
            return
        done = sum(1 for h in hosts if h.status == "done")
        await close_rollout(session, r, "completed", f"{done} of {len(hosts)} hosts patched")
    await audit.log_action(user_id=None, username="posture-service", action="posture.patch.complete",
                           resource_type="patch_rollout", resource_id=rollout_id,
                           details={"done": done, "hosts": len(hosts)}, result="success")
    await clients.notify(user_id=r.created_by, severity="info", title=f"Patch rollout completed: {r.name}",
                         message=r.status_reason, source_id=r.id)


# ── package operations outside a rollout ─────────────────────────────────────

def op_action(op: str) -> str:
    """automation-service action for a host operation: reboots have their own executor."""
    return "host_reboot" if op == "reboot" else "package_ops"


async def poll_package_ops() -> None:
    async with SessionLocal() as session:
        ids = (await session.execute(select(PackageOp.id).where(
            PackageOp.status.in_(("queued", "running", "awaiting_approval"))))).scalars().all()
    for op_id in ids:
        try:
            await _poll_package_op(op_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("package operation %s could not be polled", op_id)


async def _poll_package_op(op_id: str) -> None:
    async with SessionLocal() as session:
        op = await session.get(PackageOp, op_id)
        await session.commit()
        try:
            run = await clients.job_run(op.job_run_id) if op.job_run_id else None
        except httpx.HTTPError:
            return
        status = (run or {}).get("status")
        tries = (op.result or {}).get("tries", 0)
        orphaned = status in ("pending", "running") and not run.get("live") \
            and now() - (aware(op.created_at) or now()) > RELAUNCH_AFTER
        if (orphaned or status in ("error", "timeout")) and tries < MAX_TRIES:
            # The follower died, not the operation: same token, the host re-attaches.
            try:
                new = await clients.host_op_run(action=op_action(op.op), op=op.op, args=op.args, host_id=op.host_id,
                                                token=f"op-{op.id}", requested_by=op.requested_by, approval=None,
                                                accountable=(op.result or {}).get("accountable") or [])
            except clients.UpstreamError as exc:
                if exc.status in _BUSY_RETRY:
                    return
                op.status, op.error, op.finished_at = "failed", exc.detail[:2000], now()
                await session.commit()
                return
            op.job_run_id, op.result = new["run_id"], {**(op.result or {}), "tries": tries + 1}
            op.status = "awaiting_approval" if new.get("status") == "pending_approval" else "queued"
            await session.commit()
            return
        if run is not None and status not in _DONE:
            op.status = {"pending_approval": "awaiting_approval", "running": "running"}.get(status, "queued")
            await session.commit()
            return
        op.finished_at = now()
        if op.op == "reboot":
            # A reboot prints no package changes; its reason stays with the result.
            op.result = {"exit_code": (run or {}).get("exit_code"), "reason": (op.result or {}).get("reason", "")}
        else:
            try:
                ch = core.parse_changes((run or {}).get("output_lines") or [])
                op.result = {"exit_code": run.get("exit_code"), "changes": core.pair_changes(ch), "held": ch["held"],
                             "plan": ch["plan"][:2000]}
            except core.PayloadError as exc:
                op.result, op.error = {"exit_code": (run or {}).get("exit_code")}, str(exc)
        if run is not None and status == "success" and not op.error:
            op.status = "ok"
        else:
            op.status = "failed"
            op.error = (_tail(run) if run else "the job run no longer exists")[:2000]
        await session.commit()
        refresh = op.status == "ok" and op.op not in ("plan", "refresh")
        host_id, user = op.host_id, op.requested_by
    if refresh:
        try:
            await collector.request_collection([{"id": host_id, "os_type": "linux"}], user)
        except Exception:
            logger.warning("facts refresh after package op %s failed", op_id)


async def tick() -> None:
    for job in (poll_package_ops, extend_maintenance):
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s failed", job.__name__)
    async with SessionLocal() as session:
        ids = (await session.execute(select(PatchRollout.id).where(PatchRollout.status.in_(LIVE_ROLLOUT)))).scalars().all()
        extra = (await session.execute(select(RolloutHost.rollout_id).where(RolloutHost.status == "running").distinct())).scalars().all()
    for rid in dict.fromkeys([*ids, *extra]):
        try:
            await advance_rollout(rid)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("rollout %s tick failed", rid)


async def loop() -> None:
    interval = max(1, get_settings().posture_rollout_tick_seconds)
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("rollout tick failed")
        await asyncio.sleep(interval)
