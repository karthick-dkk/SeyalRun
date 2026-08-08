"""In-UI notifications: list/read/dismiss + per-user severity preferences.

Every authenticated caller manages only their own notifications/preferences
(scoped by X-User-Id, forwarded by api-gateway) — no role gate beyond auth,
same self-service tier as /auth (see api-gateway rbac.py's _ALWAYS).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, WebSocket, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session, SessionLocal
from ..deps import get_user_id, require_service_token
from ..models import ZANotification, ZANotificationPreference

router = APIRouter(prefix="/notifications", tags=["notifications"], dependencies=[Depends(require_service_token)])

# A failure notification must always reach the user — never mutable via preferences.
_MUTABLE_SEVERITIES = ("info", "medium")


def _out(n: ZANotification) -> dict:
    return {
        "id": n.id, "severity": n.severity, "title": n.title, "message": n.message,
        "source_type": n.source_type, "source_id": n.source_id,
        "read": n.read_at is not None,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("")
async def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    user_id: str = Depends(get_user_id),
    session: AsyncSession = Depends(get_session),
):
    # user_id match OR broadcast (user_id IS NULL) — a run with no resolvable
    # triggering user notifies everyone rather than notifying no one.
    stmt = select(ZANotification).where(
        (ZANotification.user_id == user_id) | (ZANotification.user_id.is_(None))
    )
    if unread_only:
        stmt = stmt.where(ZANotification.read_at.is_(None))
    # Unread first (regardless of age), newest-first within each group — otherwise
    # an old unread notification can be pushed out of this capped list by newer
    # already-read ones while still counting toward unread_count below, showing a
    # badge the caller can never actually click through to (confirmed live: badge
    # read 9 while the list — sorted purely by recency — showed only 1 item).
    stmt = stmt.order_by(ZANotification.read_at.is_(None).desc(), ZANotification.created_at.desc()).limit(min(limit, 200))
    result = await session.execute(stmt)
    items = [_out(n) for n in result.scalars().all()]

    count_stmt = select(func.count()).select_from(ZANotification).where(
        ((ZANotification.user_id == user_id) | (ZANotification.user_id.is_(None)))
        & ZANotification.read_at.is_(None)
    )
    unread_count = (await session.execute(count_stmt)).scalar_one()
    return {"items": items, "unread_count": unread_count}


async def _own_notification(session: AsyncSession, notif_id: str, user_id: str) -> ZANotification:
    n = await session.get(ZANotification, notif_id)
    if n is None or (n.user_id is not None and n.user_id != user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return n


@router.post("/{notif_id}/read")
async def mark_read(notif_id: str, user_id: str = Depends(get_user_id), session: AsyncSession = Depends(get_session)):
    n = await _own_notification(session, notif_id, user_id)
    n.read_at = datetime.now(timezone.utc)
    await session.commit()
    return _out(n)


@router.post("/read-all")
async def mark_all_read(user_id: str = Depends(get_user_id), session: AsyncSession = Depends(get_session)):
    stmt = select(ZANotification).where(
        ((ZANotification.user_id == user_id) | (ZANotification.user_id.is_(None)))
        & ZANotification.read_at.is_(None)
    )
    result = await session.execute(stmt)
    now = datetime.now(timezone.utc)
    for n in result.scalars().all():
        n.read_at = now
    await session.commit()
    return {"ok": True}


@router.delete("/{notif_id}", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(notif_id: str, user_id: str = Depends(get_user_id), session: AsyncSession = Depends(get_session)):
    n = await _own_notification(session, notif_id, user_id)
    await session.delete(n)
    await session.commit()


class PreferencesUpdate(BaseModel):
    muted_severities: list[str] = []


@router.get("/preferences")
async def get_preferences(user_id: str = Depends(get_user_id), session: AsyncSession = Depends(get_session)):
    pref = await session.get(ZANotificationPreference, user_id)
    return {"muted_severities": list(pref.muted_severities) if pref else []}


@router.put("/preferences")
async def set_preferences(
    payload: PreferencesUpdate,
    user_id: str = Depends(get_user_id),
    session: AsyncSession = Depends(get_session),
):
    muted = [s for s in payload.muted_severities if s in _MUTABLE_SEVERITIES]
    pref = await session.get(ZANotificationPreference, user_id)
    if pref is None:
        pref = ZANotificationPreference(user_id=user_id, muted_severities=muted)
        session.add(pref)
    else:
        pref.muted_severities = muted
    await session.commit()
    return {"muted_severities": muted}


async def ws_notifications(websocket: WebSocket) -> None:
    """Live delivery — api-gateway's ws_proxy resolves the caller's JWT and forwards
    X-User-Id on the upstream WS handshake (same pattern as the terminal/job-log
    proxies), so identity here is a header read, not a second auth check."""
    user_id = websocket.headers.get("x-user-id", "")
    await websocket.accept()

    async with SessionLocal() as session:
        pref = await session.get(ZANotificationPreference, user_id) if user_id else None
    muted = set(pref.muted_severities) if pref else set()

    settings = get_settings()
    async with aioredis.from_url(settings.redis_url, decode_responses=True) as sub_client:
        async with sub_client.pubsub() as pubsub:
            channels = [f"notifications:{user_id}", "notifications:broadcast"] if user_id else ["notifications:broadcast"]
            await pubsub.subscribe(*channels)
            try:
                while True:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=25)
                    if msg is None:
                        await websocket.send_text(json.dumps({"type": "ping"}))
                        continue
                    payload = json.loads(msg["data"])
                    # "critical" is never mutable (see PreferencesUpdate above) — this
                    # filter only ever drops info/medium.
                    if payload.get("severity") in muted:
                        continue
                    await websocket.send_text(json.dumps({"type": "notification", **payload}))
            except Exception:
                pass
