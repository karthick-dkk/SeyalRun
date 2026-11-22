"""GET posture/feeds, POST posture/feeds/import, POST posture/feeds/{id}/activate."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, feedbundle, feeds
from ..config import get_settings
from ..database import get_session
from ..deps import Caller, get_caller, require_admin
from ..models import FeedSnapshot

router = APIRouter(prefix="/posture", tags=["posture-feeds"])


@router.get("/feeds")
async def list_feeds(request: Request, _caller: Caller = Depends(get_caller),
                     session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(FeedSnapshot).order_by(FeedSnapshot.imported_at.desc()))
    out = [feeds.snapshot_out(s) for s in res.scalars().all()]
    request.state.audit["details"] = {"count": len(out)}
    return out


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
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                    detail=f"bundle exceeds POSTURE_FEED_MAX_BYTES ({max_bytes} bytes)")
            h.update(chunk)
            await asyncio.to_thread(out.write, chunk)  # keep disk I/O off the event loop
    if size == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload")
    return h.hexdigest(), size


@router.post("/feeds/import", status_code=status.HTTP_201_CREATED)
async def import_feed(request: Request, file: UploadFile = File(...),
                      caller: Caller = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    settings = get_settings()
    ad = request.state.audit
    ad["details"] = {"filename": (file.filename or "")[:200]}
    work = feeds.new_work_dir()
    upload_path = work.with_suffix(".upload")
    handed_off = False
    try:
        bundle_sha, size = await _save_upload(file, upload_path, settings.posture_feed_max_bytes)
        ad["details"].update({"bundle_sha256": bundle_sha, "size": size})
        try:
            bundle = await asyncio.to_thread(feeds.stage_and_verify, upload_path, work)
        except feedbundle.BundleError as exc:
            ad["details"]["reason"] = str(exc)[:500]
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        finally:
            upload_path.unlink(missing_ok=True)
        ad["details"].update({"version": bundle.version, "signed_by_key": bundle.signed_by})

        existing = (await session.execute(
            select(FeedSnapshot).where(FeedSnapshot.version == bundle.version))).scalar_one_or_none()
        if existing is not None:
            if existing.status != "failed":
                ad["details"]["reason"] = "duplicate version"
                raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                    detail=f"feed version {bundle.version} is already imported "
                                           f"(snapshot {existing.id}, {existing.status})")
            await session.execute(delete(FeedSnapshot).where(FeedSnapshot.id == existing.id))

        snap = FeedSnapshot(
            version=bundle.version, generated_at=bundle.generated_at, imported_by=caller.user_id,
            imported_by_name=caller.username[:200], signed_by_key=bundle.signed_by[:200],
            bundle_sha256=bundle_sha, manifest=bundle.manifest, status="importing",
            stats={name: {"entries": 0, "rows": 0} for name in bundle.files}, active=False,
        )
        session.add(snap)
        await session.flush()
        # Fail closed: a snapshot nobody can account for is refused, not created.
        try:
            await audit.log_action(
                user_id=caller.user_id, username=caller.username, action="posture.feed.import",
                resource_type="feed_snapshot", resource_id=snap.id, ip_address=caller.ip,
                details={**ad["details"], "manifest_sha256": bundle.manifest_sha256,
                         "sources": {k: len(v) for k, v in bundle.files.items()}},
                result="accepted", critical=True,
            )
        except audit.AuditWriteError as exc:
            await session.rollback()
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="audit log unavailable; import refused") from exc
        await session.commit()
        request.state.audited = True
        feeds.start_import(snap.id, bundle, work, (caller.user_id, caller.username))
        handed_off = True
        return feeds.snapshot_out(snap)
    finally:
        upload_path.unlink(missing_ok=True)
        if not handed_off:
            feedbundle.remove_tree(work)


@router.post("/feeds/{snapshot_id}/activate")
async def activate_feed(snapshot_id: str, request: Request, caller: Caller = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    snap = await session.get(FeedSnapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="feed snapshot not found")
    request.state.audit["details"] = {"version": snap.version}
    if snap.status != "ready":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"snapshot {snap.version} is {snap.status}; only a ready snapshot can be activated")
    previous = (await session.execute(
        select(FeedSnapshot.version).where(FeedSnapshot.active.is_(True)))).scalars().all()
    await session.execute(update(FeedSnapshot).where(FeedSnapshot.id != snapshot_id).values(active=False))
    snap.active = True
    snap.activated_at = feeds.now()
    snap.activated_by = caller.user_id
    await session.flush()
    try:
        await audit.log_action(
            user_id=caller.user_id, username=caller.username, action="posture.feed.activate",
            resource_type="feed_snapshot", resource_id=snap.id, ip_address=caller.ip,
            details={"version": snap.version, "previous_active": list(previous)},
            result="success", critical=True,
        )
    except audit.AuditWriteError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="audit log unavailable; activation refused") from exc
    await session.commit()
    await session.refresh(snap)
    request.state.audited = True
    return feeds.snapshot_out(snap)
