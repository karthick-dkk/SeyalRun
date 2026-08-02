"""Live session viewer (PCI DSS Phase D). Read-only: a spectator receives a
copy of the primary session's "output" frames (see terminal.py::TeeWebSocket)
and anything a spectator sends is dropped server-side, never forwarded to the
live SSH connection — this is strictly observation, not shared control.

Gated the same way DELETE /ssh/sessions/{id} (kill-session) already is:
admin/superadmin, or the session's own owner. No separate per-host
ZAAuthorization "spectate" grant — being able to already terminate any
session is a strictly higher privilege than being able to merely watch one,
so extending that same admin/superadmin precedent here adds no new exposure.
"""

from __future__ import annotations

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from libs.servicetoken import ServiceTokenError, verify

from ..config import get_settings
from ..database import SessionLocal
from ..models import ZASSHSession

logger = logging.getLogger(__name__)


async def handle_spectate(websocket: WebSocket, session_id: str, spectators: dict[str, list]) -> None:
    settings = get_settings()

    svc_token = websocket.headers.get("x-service-token", "")
    try:
        verify(svc_token, "terminal-service", settings.service_jwt_secret)
    except (ServiceTokenError, Exception):
        await websocket.close(code=4401)
        return

    user_id = websocket.headers.get("x-user-id", "")
    role = websocket.headers.get("x-user-role", "user")
    if not user_id:
        await websocket.close(code=4401)
        return

    async with SessionLocal() as db:
        result = await db.execute(select(ZASSHSession).where(ZASSHSession.id == session_id))
        sess = result.scalar_one_or_none()
    if sess is None:
        await websocket.close(code=4404)
        return
    if role not in ("admin", "superadmin") and sess.user_id != user_id:
        await websocket.close(code=4403)
        return
    if sess.status != "active":
        await websocket.close(code=4400)
        return

    await websocket.accept()
    subs = spectators.setdefault(session_id, [])
    subs.append(websocket)
    logger.info("spectator joined", extra={"session_id": session_id, "spectator_user_id": user_id})

    try:
        while True:
            # Read-only: any frame a spectator sends (keystrokes, resize, etc.) is
            # deliberately dropped here — never forwarded to the live SSH connection.
            msg = await websocket.receive_text()
            try:
                json.loads(msg)
            except (TypeError, ValueError):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        try:
            subs.remove(websocket)
        except ValueError:
            pass
        # Drop the dict entry once its subscriber list is empty — otherwise every
        # session_id ever spectated leaves a permanent empty-list entry for the
        # life of the process, growing unboundedly on a busy PAM system.
        if not subs:
            spectators.pop(session_id, None)
        logger.info("spectator left", extra={"session_id": session_id, "spectator_user_id": user_id})
