"""Findings on the database: scanner registry, result import, the native-match
queue and EPSS/KEV re-scoring. The decisions themselves live in findings_core.

Memory stays bounded on a small box: an import is parsed once into a JSONL
spool on the data volume (so a malformed file is refused before anything is
written) and then applied in batches; native matching fetches feed rows a few
hundred package names at a time; every write is a batched insert/update.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from libs.pluginbase import HostRef, ScanParseError, ScanRecord, VulnScanner, discover_plugins
from sqlalchemy import func, insert, or_, select, update

from . import audit, clients, findings_core
from .database import SessionLocal
from .models import (
    FeedEpss,
    FeedKev,
    FeedOsv,
    FeedOval,
    FeedSnapshot,
    PostureFinding,
    PostureFindingEvent,
    PostureHost,
    PostureInstalledPackage,
    PostureScan,
    PostureUnmatched,
    PostureUnmatchedItem,
)
from .refs import safe_urls

logger = logging.getLogger(__name__)

BATCH = 1000
IN_CHUNK = 500
PKG_CHUNK = 200
_registry: dict[str, VulnScanner] | None = None
# One writer at a time for findings: an import batch, an assignment and a
# native run touching the same (host, CVE, package) would otherwise race on the
# unique key between their reads and their commits. Single-process service.
WRITE_LOCK = asyncio.Lock()


def now() -> datetime:
    return datetime.now(UTC)


def registry() -> dict[str, VulnScanner]:
    global _registry
    if _registry is None:
        _registry = discover_plugins(f"{__package__}.plugins.scanners", VulnScanner)
        logger.info("scanner plugins: %s", ", ".join(sorted(_registry)) or "(none)")
    return _registry


def _chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ── feed snapshot used for matching and scoring ──────────────────────────────

async def active_snapshot(session) -> FeedSnapshot | None:
    return (await session.execute(
        select(FeedSnapshot).where(FeedSnapshot.active.is_(True)).limit(1))).scalar_one_or_none()


async def scoring_snapshot_id(session) -> str | None:
    """EPSS/KEV come from the active snapshot; before any activation, from the
    most recently imported ready one, so scores exist as soon as a bundle does."""
    snap = await active_snapshot(session)
    if snap is not None:
        return snap.id
    return (await session.execute(
        select(FeedSnapshot.id).where(FeedSnapshot.status == "ready")
        .order_by(FeedSnapshot.imported_at.desc()).limit(1))).scalar_one_or_none()


async def enrichment_for(session, snapshot_id: str | None, cves: list[str]) -> dict:
    out: dict[str, tuple[float | None, float | None, bool]] = {}
    if not snapshot_id or not cves:
        return out
    for chunk in _chunks(sorted(set(cves)), IN_CHUNK):
        for cve_id, score, pct in (await session.execute(
                select(FeedEpss.cve_id, FeedEpss.score, FeedEpss.percentile)
                .where(FeedEpss.snapshot_id == snapshot_id, FeedEpss.cve_id.in_(chunk)))).all():
            out[cve_id] = (score, pct, False)
        for (cve_id,) in (await session.execute(
                select(FeedKev.cve_id).where(FeedKev.snapshot_id == snapshot_id, FeedKev.cve_id.in_(chunk)))).all():
            e, p, _ = out.get(cve_id, (None, None, False))
            out[cve_id] = (e, p, True)
    return out


# ── the Store findings_core writes through ───────────────────────────────────

_FINDING_COLS = ("id", "host_id", "cve_id", "package", "installed_version", "fixed_version", "severity",
                 "cvss", "epss", "epss_percentile", "kev", "risk_score", "sources", "summary", "references",
                 "first_seen", "last_seen", "status", "status_reason", "fixed_at")


def finding_dict(f: PostureFinding) -> dict:
    return {c: getattr(f, c) for c in _FINDING_COLS}


class DbStore:
    def __init__(self, session, snapshot_id: str | None):
        self.session = session
        self.snapshot_id = snapshot_id

    async def load(self, host_id: str, cve_ids: list[str]) -> list[dict]:
        out: list[dict] = []
        for chunk in _chunks(cve_ids, IN_CHUNK):
            res = await self.session.execute(select(PostureFinding).where(
                PostureFinding.host_id == host_id, PostureFinding.cve_id.in_(chunk)))
            out += [finding_dict(f) for f in res.scalars().all()]
        return out

    async def insert(self, rows: list[dict]) -> None:
        ts = now()
        for chunk in _chunks(rows, BATCH):
            await self.session.execute(insert(PostureFinding), [{**r, "updated_at": ts} for r in chunk])

    async def update(self, rows: list[dict]) -> None:
        ts = now()
        for chunk in _chunks(rows, BATCH):
            await self.session.execute(update(PostureFinding), [
                {**{c: r[c] for c in _FINDING_COLS if c not in ("host_id", "first_seen")}, "updated_at": ts}
                for r in chunk])

    async def add_events(self, rows: list[dict]) -> None:
        for chunk in _chunks(rows, BATCH):
            await self.session.execute(insert(PostureFindingEvent), chunk)

    async def enrichment(self, cve_ids: list[str]) -> dict:
        return await enrichment_for(self.session, self.snapshot_id, cve_ids)


# ── per-host package context ─────────────────────────────────────────────────

async def host_packages(session, host_id: str) -> tuple[PostureHost | None, list[dict]]:
    host = await session.get(PostureHost, host_id)
    res = await session.execute(
        select(PostureInstalledPackage.name, PostureInstalledPackage.version,
               PostureInstalledPackage.source, PostureInstalledPackage.source_version)
        .where(PostureInstalledPackage.host_id == host_id))
    return host, [{"name": n, "version": v, "source": s or "", "source_version": sv or ""}
                  for n, v, s, sv in res.all()]


def canonicaliser(host: PostureHost | None, installed: list[dict]):
    """Package name -> the name findings are keyed by on this host."""
    from .plugins.scanners.native import binary_to_source, scheme_for

    if host is not None and scheme_for(host.distro_family, host.pkg_manager) == "deb":
        b2s = binary_to_source(installed)
        return lambda p: b2s.get(p, p)
    return lambda p: p


# ── import ───────────────────────────────────────────────────────────────────

def spool_records(scanner: VulnScanner, src: Path, spool: Path, max_records: int) -> int:
    """Blocking: parse the upload fully into a JSONL spool. Raises ScanParseError."""
    n = 0
    try:
        with open(spool, "w", encoding="utf-8") as out:
            for rec in scanner.parse(str(src)):
                n += 1
                if n > max_records:
                    raise ScanParseError(f"more than {max_records} findings in one file "
                                         f"(POSTURE_SCAN_MAX_RECORDS)")
                out.write(json.dumps(asdict(rec), default=str) + "\n")
    except ScanParseError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, OSError, EOFError) as exc:
        # A parser tripping over a structurally odd file is still "bad input".
        raise ScanParseError(f"could not parse the file as {scanner.name} results "
                             f"({type(exc).__name__}: {str(exc)[:200]})") from exc
    return n


def _read_spool(spool: Path, n: int) -> Iterable[list[ScanRecord]]:
    batch: list[ScanRecord] = []
    with open(spool, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            d["host"] = HostRef(**d["host"])
            batch.append(ScanRecord(**d))
            if len(batch) >= n:
                yield batch
                batch = []
    if batch:
        yield batch


async def host_index() -> findings_core.HostIndex:
    inventory = await clients.inventory_hosts()
    async with SessionLocal() as session:
        names = dict((await session.execute(select(PostureHost.host_id, PostureHost.hostname))).all())
    return findings_core.HostIndex(inventory, names)


async def _assign_records(session, host_id: str, records: list[ScanRecord], scanner: str,
                          actor: str, ctx_cache: dict, snapshot_id: str | None) -> tuple[int, int]:
    if host_id not in ctx_cache:
        host, installed = await host_packages(session, host_id)
        ctx_cache[host_id] = canonicaliser(host, installed)
    created, updated, _ = await findings_core.apply_records(
        DbStore(session, snapshot_id), host_id, records, scanner, now(), canon=ctx_cache[host_id], actor=actor)
    return created, updated


async def _queue_unmatched(session, scan_id: str, scanner: str, ref: HostRef, records: list[ScanRecord],
                           groups: dict) -> None:
    key = ref.key()
    g = groups.get(key)
    if g is None:
        g = {"id": str(uuid.uuid4()), "cves": set()}
        groups[key] = g
        session.add(PostureUnmatched(
            id=g["id"], scan_id=scan_id, scanner=scanner, reported_host=findings_core.reported_host_label(ref),
            reported_ip=(ref.ip or None), reported_ref=asdict(ref), cve_count=0, received_at=now(),
            status="pending"))
        await session.flush()
    rows = []
    for r in records:
        g["cves"].add(r.cve_id)
        rows.append({"unmatched_id": g["id"], "cve_id": r.cve_id[:64], "package": (r.package or "")[:255],
                     "installed_version": (r.installed_version or "")[:255],
                     "fixed_version": (r.fixed_version or None), "severity": (r.severity or "")[:32],
                     "cvss": r.cvss, "summary": r.summary, "references": safe_urls(r.references)})
    for chunk in _chunks(rows, BATCH):
        await session.execute(insert(PostureUnmatchedItem), chunk)


async def apply_import(scan_id: str, scanner_name: str, spool: Path, override_host: str | None,
                       actor: str) -> dict:
    """Apply a spooled import in batches: correlate each record's host, upsert
    findings, queue what does not correlate. Returns the response counts."""
    index = None if override_host else await host_index()
    created = updated = 0
    groups: dict[tuple, dict] = {}
    ctx_cache: dict[str, object] = {}
    it = _read_spool(spool, BATCH)
    async with SessionLocal() as session:
        snapshot_id = await scoring_snapshot_id(session)
    while True:
        batch = await asyncio.to_thread(next, it, None)
        if batch is None:
            break
        per_host: dict[str, list[ScanRecord]] = {}
        orphans: dict[tuple, tuple[HostRef, list[ScanRecord]]] = {}
        for rec in batch:
            hid = override_host or index.resolve(rec.host)
            if hid:
                per_host.setdefault(hid, []).append(rec)
            else:
                orphans.setdefault(rec.host.key(), (rec.host, []))[1].append(rec)
        async with WRITE_LOCK, SessionLocal() as session:
            for hid, recs in per_host.items():
                c, u = await _assign_records(session, hid, recs, scanner_name, actor, ctx_cache, snapshot_id)
                created, updated = created + c, updated + u
            for ref, recs in orphans.values():
                await _queue_unmatched(session, scan_id, scanner_name, ref, recs, groups)
            await session.commit()
    if groups:
        async with SessionLocal() as session:
            for g in groups.values():
                await session.execute(update(PostureUnmatched).where(PostureUnmatched.id == g["id"])
                                      .values(cve_count=len(g["cves"])))
            await session.commit()
    return {"findings_created": created, "findings_updated": updated, "unmatched": len(groups)}


async def assign_unmatched(unmatched_id: str, host_id: str, user_id: str, username: str) -> int:
    """Turn a queued result into findings on ``host_id``. Returns findings created."""
    async with WRITE_LOCK, SessionLocal() as session:
        u = await session.get(PostureUnmatched, unmatched_id)
        snapshot_id = await scoring_snapshot_id(session)
        scanner = u.scanner
        created = 0
        ctx_cache: dict[str, object] = {}
        last_id = 0
        while True:
            items = (await session.execute(
                select(PostureUnmatchedItem).where(PostureUnmatchedItem.unmatched_id == unmatched_id,
                                                   PostureUnmatchedItem.id > last_id)
                .order_by(PostureUnmatchedItem.id).limit(BATCH))).scalars().all()
            if not items:
                break
            last_id = items[-1].id
            recs = [ScanRecord(host=HostRef(host_id=host_id), cve_id=i.cve_id, package=i.package,
                               installed_version=i.installed_version, fixed_version=i.fixed_version,
                               severity=i.severity, cvss=i.cvss, summary=i.summary,
                               references=list(i.references or [])) for i in items]
            c, _ = await _assign_records(session, host_id, recs, scanner, username or user_id, ctx_cache,
                                         snapshot_id)
            created += c
        u.status = "assigned"
        u.assigned_host_id = host_id
        u.assigned_at = now()
        u.assigned_by = user_id
        await session.commit()
    return created


# ── scans table ──────────────────────────────────────────────────────────────

async def start_scan(scanner: str, kind: str, *, host_id: str | None = None, filename: str = "",
                     sha256: str = "", user_id: str | None = None, username: str = "") -> str:
    async with SessionLocal() as session:
        scan = PostureScan(scanner=scanner, kind=kind, host_id=host_id, filename=filename[:255],
                           sha256=sha256, status="running", started_at=now(), requested_by=user_id,
                           requested_by_name=(username or "")[:200])
        session.add(scan)
        await session.commit()
        return scan.id


async def finish_scan(scan_id: str, status: str, error: str | None = None, **counts) -> None:
    async with SessionLocal() as session:
        await session.execute(update(PostureScan).where(PostureScan.id == scan_id).values(
            status=status, error=(error or None) and error[:2000], finished_at=now(),
            **{k: int(v) for k, v in counts.items()
               if k in ("findings_created", "findings_updated", "findings_fixed", "unmatched")}))
        await session.commit()


async def last_runs() -> dict[str, tuple[datetime | None, str | None]]:
    async with SessionLocal() as session:
        sub = (select(PostureScan.scanner, func.max(PostureScan.started_at).label("at"))
               .group_by(PostureScan.scanner).subquery())
        res = await session.execute(
            select(PostureScan.scanner, PostureScan.started_at, PostureScan.status)
            .join(sub, (sub.c.scanner == PostureScan.scanner) & (sub.c.at == PostureScan.started_at)))
        return {name: (at, st) for name, at, st in res.all()}


# ── native matching ──────────────────────────────────────────────────────────

_native_pending: dict[str, tuple[str | None, str]] = {}
_native_task: asyncio.Task | None = None


def enqueue_native(host_ids: list[str], requested_by: str | None, requested_by_name: str) -> int:
    """Queue hosts for a native match (deduplicated). Returns how many were added."""
    global _native_task
    added = 0
    for hid in host_ids:
        if hid not in _native_pending:
            _native_pending[hid] = (requested_by, requested_by_name)
            added += 1
    if _native_pending and (_native_task is None or _native_task.done()):
        _native_task = asyncio.create_task(_native_worker())
    return added


async def _native_worker() -> None:
    while _native_pending:
        host_id = next(iter(_native_pending))
        by, name = _native_pending.pop(host_id)
        try:
            await run_native(host_id, by, name)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("native match of host %s failed", host_id)


async def _feed_rows(session, snapshot_id: str, names: list[str], host: PostureHost) -> tuple[list, list]:
    from .plugins.scanners import native as nat

    pats = nat.osv_like_patterns(host.distro_id, host.distro_family, host.distro_version)
    oval_pat = nat.oval_like_pattern(host.distro_id, host.distro_family, host.distro_version)
    osv: list[dict] = []
    oval: list[dict] = []
    if pats:
        res = await session.execute(
            select(FeedOsv.vuln_id, FeedOsv.cve_id, FeedOsv.ecosystem, FeedOsv.package, FeedOsv.range_type,
                   FeedOsv.introduced, FeedOsv.fixed, FeedOsv.last_affected, FeedOsv.severity,
                   FeedOsv.cvss, FeedOsv.summary)
            .where(FeedOsv.snapshot_id == snapshot_id, FeedOsv.package.in_(names),
                   or_(*[FeedOsv.ecosystem.like(p) for p in pats])))
        osv = [dict(r._mapping) for r in res.all()]
    if oval_pat:
        res = await session.execute(
            select(FeedOval.definition_id, FeedOval.cve_id, FeedOval.platform, FeedOval.package,
                   FeedOval.fixed_version, FeedOval.severity, FeedOval.cvss, FeedOval.summary)
            .where(FeedOval.snapshot_id == snapshot_id, FeedOval.package.in_(names),
                   FeedOval.platform.like(oval_pat)))
        oval = [dict(r._mapping) for r in res.all()]
    return osv, oval


async def _covered(session, snapshot_id: str, host: PostureHost) -> bool:
    """Does the snapshot carry any advisory for this host's distro release?"""
    from .plugins.scanners import native as nat

    args = (host.distro_id, host.distro_family, host.distro_version)
    for p in nat.osv_like_patterns(*args):
        res = await session.execute(select(FeedOsv.ecosystem).where(
            FeedOsv.snapshot_id == snapshot_id, FeedOsv.ecosystem.like(p)).distinct().limit(50))
        if any(nat.ecosystem_matches(e, *args) for (e,) in res.all()):
            return True
    pat = nat.oval_like_pattern(*args)
    if pat:
        res = await session.execute(select(FeedOval.platform).where(
            FeedOval.snapshot_id == snapshot_id, FeedOval.platform.like(pat)).distinct().limit(50))
        if any(nat.oval_platform_matches(pl, *args) for (pl,) in res.all()):
            return True
    return False


async def run_native(host_id: str, requested_by: str | None = None, requested_by_name: str = "") -> dict:
    """Match one host. Records a posture_scans row whatever happens."""
    from .plugins.scanners import native as nat

    scanner = registry().get("native")
    scan_id = await start_scan("native", "native", host_id=host_id, user_id=requested_by,
                               username=requested_by_name)
    if scanner is None or "native" in await clients.disabled_scanners():
        await finish_scan(scan_id, "skipped", "native scanner is disabled")
        return {"status": "skipped"}
    async with SessionLocal() as session:
        host, installed = await host_packages(session, host_id)
        snap = await active_snapshot(session)
        if host is None or host.collected_at is None or not installed:
            await finish_scan(scan_id, "skipped", "no host facts collected yet")
            return {"status": "skipped"}
        scheme = nat.scheme_for(host.distro_family, host.pkg_manager)
        if scheme is None:
            await finish_scan(scan_id, "skipped", f"unsupported distribution '{host.distro_id}'")
            return {"status": "skipped"}
        if snap is None:
            await finish_scan(scan_id, "skipped", "no active feed snapshot")
            return {"status": "skipped"}
        snap_id, snap_version = snap.id, snap.version
        packages = nat.canonical_packages(installed, scheme)
        hostd = {"host_id": host_id, "hostname": host.hostname, "distro_id": host.distro_id,
                 "distro_family": host.distro_family, "distro_version": host.distro_version,
                 "pkg_manager": host.pkg_manager}
        records: list[ScanRecord] = []
        names = sorted(packages)
        for chunk in _chunks(names, PKG_CHUNK):
            osv, oval = await _feed_rows(session, snap_id, chunk, host)
            sub = {n: packages[n] for n in chunk}
            records += scanner.match(hostd, sub, osv, oval)
        covered = await _covered(session, snap_id, host)
        distro = f"{host.distro_id} {host.distro_version}"
    async with WRITE_LOCK, SessionLocal() as session:
        scoring = snap_id
        t = now()
        store = DbStore(session, scoring)
        created, updated, seen = await findings_core.apply_records(
            store, host_id, records, "native", t, actor="native")
        current = [finding_dict(f) for f in (await session.execute(select(PostureFinding).where(
            PostureFinding.host_id == host_id,
            PostureFinding.status.in_((findings_core.OPEN, findings_core.ACCEPTED))))).scalars().all()]
        fixed = findings_core.fixed_after_native(current, seen, packages, scheme, covered)
        if fixed:
            for chunk in _chunks(fixed, BATCH):
                await session.execute(update(PostureFinding), [
                    {"id": r["id"], "status": findings_core.FIXED, "fixed_at": t, "updated_at": t}
                    for r, _ in chunk])
            await store.add_events([{"finding_id": r["id"], "at": t, "event": "fixed", "by": "native",
                                     "details": {"reason": why}} for r, why in fixed])
        await session.commit()
    warn = None if covered else (f"active feed has no advisories for {distro}; "
                                 f"nothing was marked fixed from absence")
    await finish_scan(scan_id, "ok", warn, findings_created=created, findings_updated=updated,
                      findings_fixed=len(fixed))
    await audit.log_action(
        user_id=requested_by, username=requested_by_name or "posture-service", action="posture.native.scan",
        resource_type="host", resource_id=host_id,
        details={"scan_id": scan_id, "snapshot": snap_version, "created": created, "updated": updated,
                 "fixed": len(fixed), "covered": covered},
        result="success")
    return {"status": "ok", "created": created, "updated": updated, "fixed": len(fixed), "covered": covered}


async def hosts_with_facts() -> list[str]:
    async with SessionLocal() as session:
        res = await session.execute(select(PostureHost.host_id).where(PostureHost.collected_at.is_not(None)))
        return [h for (h,) in res.all()]


# ── re-score after a feed activation ─────────────────────────────────────────

_tasks: set[asyncio.Task] = set()


async def rescore_all() -> int:
    """Refresh EPSS/KEV/risk on every open or accepted finding from the scoring
    snapshot, in id-ordered batches."""
    from .risk import risk_score

    n = 0
    last = ""
    async with SessionLocal() as session:
        snapshot_id = await scoring_snapshot_id(session)
    while True:
        async with SessionLocal() as session:
            rows = (await session.execute(
                select(PostureFinding.id, PostureFinding.cve_id, PostureFinding.cvss, PostureFinding.severity)
                .where(PostureFinding.status != findings_core.FIXED, PostureFinding.id > last)
                .order_by(PostureFinding.id).limit(BATCH))).all()
            if not rows:
                break
            last = rows[-1][0]
            enrich = await enrichment_for(session, snapshot_id, [r[1] for r in rows])
            ups = []
            for fid, cve_id, cvss, sev in rows:
                e, p, k = enrich.get(cve_id, (None, None, False))
                ups.append({"id": fid, "epss": e, "epss_percentile": p, "kev": bool(k),
                            "risk_score": risk_score(cvss, sev, p, bool(k))})
            await session.execute(update(PostureFinding), ups)
            await session.commit()
            n += len(rows)
    return n


def after_feed_activation() -> None:
    """New active feed: re-score everything, then re-match every host with facts."""
    async def _run():
        try:
            await rescore_all()
            enqueue_native(await hosts_with_facts(), None, "feed activation")
        except Exception:
            logger.exception("post-activation rescore/rematch failed")
    task = asyncio.create_task(_run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
