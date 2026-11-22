"""Feed snapshot lifecycle: import (verify -> stage -> parse in background) and
activation. The pure parts live in feedbundle.py; this module owns the DB.

An import is split so the HTTP call stays inside api-gateway's 30 s upstream
timeout: the request unpacks the bundle and verifies the signature and every
file hash synchronously (a bad bundle is a 400 there and then), records the
snapshot as ``importing`` and returns 201. Parsing and row inserts then run in a
background task, a few thousand rows per step, with the CPU-bound parsing off
the event loop. Only a ``ready`` snapshot can be activated.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, insert, select, update

from . import audit, feedbundle
from .config import get_settings
from .database import SessionLocal
from .models import FeedEpss, FeedKev, FeedOsv, FeedOval, FeedSnapshot

logger = logging.getLogger(__name__)

ROW_MODELS = {"osv": FeedOsv, "epss": FeedEpss, "kev": FeedKev, "oval": FeedOval}
BATCH = 2000
_tasks: set[asyncio.Task] = set()


def staging_root() -> Path:
    return Path(get_settings().posture_data_dir) / "staging"


def trusted_keys() -> list[feedbundle.TrustedKey]:
    s = get_settings()
    return feedbundle.load_trusted_keys(s.posture_feed_trusted_keys, s.posture_feed_trusted_keys_file)


def snapshot_out(s: FeedSnapshot) -> dict:
    stats = s.stats or {}
    return {
        "id": s.id,
        "version": s.version,
        "generated_at": s.generated_at.isoformat() if s.generated_at else None,
        "imported_at": s.imported_at.isoformat() if s.imported_at else None,
        "imported_by": s.imported_by_name or s.imported_by,
        "signed_by_key": s.signed_by_key,
        "active": bool(s.active),
        "sources": [{"name": name, "entries": int((stats.get(name) or {}).get("entries", 0))}
                    for name in feedbundle.SOURCES if name in stats],
        # Additive to the fixed contract: import progress and failure reason.
        "status": s.status,
        "error": s.error,
        "activated_at": s.activated_at.isoformat() if s.activated_at else None,
    }


def stage_and_verify(upload: Path, work: Path) -> feedbundle.VerifiedBundle:
    """Blocking — call via asyncio.to_thread. Raises BundleError."""
    s = get_settings()
    names = feedbundle.unpack_bundle(upload, work, max_unpacked=s.posture_feed_max_unpacked_bytes,
                                     max_members=s.posture_feed_max_members)
    return feedbundle.verify_bundle(work, names, trusted_keys())


async def import_rows(snapshot_id: str, bundle: feedbundle.VerifiedBundle, work: Path,
                      actor: tuple[str | None, str]) -> None:
    """Background: parse every source into rows. Marks the snapshot ready or
    failed (deleting partial rows) and audits the outcome."""
    stats: dict[str, dict] = {}
    try:
        for source, paths in bundle.files.items():
            model = ROW_MODELS[source]
            src_stats: dict = {"entries": 0, "rows": 0}
            stats[source] = src_stats
            it = feedbundle.iter_source(source, paths, src_stats)
            while True:
                rows = await asyncio.to_thread(feedbundle.take, it, BATCH)
                if not rows:
                    break
                for r in rows:
                    r["snapshot_id"] = snapshot_id
                async with SessionLocal() as session:
                    await session.execute(insert(model), rows)
                    await session.commit()
                src_stats["rows"] += len(rows)
        async with SessionLocal() as session:
            await session.execute(update(FeedSnapshot).where(FeedSnapshot.id == snapshot_id)
                                  .values(status="ready", stats=stats, error=None))
            await session.commit()
        result, err = "success", None
    except Exception as exc:
        err = str(exc) if isinstance(exc, feedbundle.BundleError) else f"{type(exc).__name__}: {exc}"
        logger.error("feed import %s failed: %s", snapshot_id, err)
        await _purge_rows(snapshot_id)
        async with SessionLocal() as session:
            await session.execute(update(FeedSnapshot).where(FeedSnapshot.id == snapshot_id)
                                  .values(status="failed", stats=stats, error=err[:2000]))
            await session.commit()
        result = "failure"
    finally:
        feedbundle.remove_tree(work)
    await audit.log_action(
        user_id=actor[0], username=actor[1], action="posture.feed.import.complete",
        resource_type="feed_snapshot", resource_id=snapshot_id,
        details={"version": bundle.version, "stats": stats, **({"error": err[:500]} if err else {})},
        result=result,
    )


async def _purge_rows(snapshot_id: str) -> None:
    for model in ROW_MODELS.values():
        try:
            async with SessionLocal() as session:
                await session.execute(delete(model).where(model.snapshot_id == snapshot_id))
                await session.commit()
        except Exception:
            logger.exception("could not purge %s rows of failed snapshot %s", model.__tablename__, snapshot_id)


def start_import(snapshot_id: str, bundle: feedbundle.VerifiedBundle, work: Path,
                 actor: tuple[str | None, str]) -> None:
    task = asyncio.create_task(import_rows(snapshot_id, bundle, work, actor))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def fail_interrupted_imports() -> None:
    """At startup: an ``importing`` snapshot whose task died with the previous
    process will never finish. Fail it (and drop its partial rows) so the admin
    sees why and re-imports, instead of a snapshot stuck forever."""
    async with SessionLocal() as session:
        res = await session.execute(select(FeedSnapshot.id).where(FeedSnapshot.status == "importing"))
        stuck = [sid for (sid,) in res.all()]
    for sid in stuck:
        await _purge_rows(sid)
        async with SessionLocal() as session:
            await session.execute(update(FeedSnapshot).where(FeedSnapshot.id == sid).values(
                status="failed", error="import interrupted by a service restart — import the bundle again"))
            await session.commit()
    feedbundle.remove_tree(staging_root())


def new_work_dir() -> Path:
    root = staging_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / uuid.uuid4().hex


def now() -> datetime:
    return datetime.now(UTC)
