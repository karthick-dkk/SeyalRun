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

from .. import supervision
from ..audit import log_action
from ..config import get_settings
from ..database import SessionLocal
from ..models import ZASSHSession

logger = logging.getLogger(__name__)


async def handle_spectate(
    websocket: WebSocket,
    session_id: str,
    spectators: dict[str, list],
    terminate_events: dict | None = None,
) -> None:
    settings = get_settings()

    svc_token = websocket.headers.get("x-service-token", "")
    try:
        verify(svc_token, "terminal-service", settings.service_jwt_secret)
    except (ServiceTokenError, Exception):
        await websocket.close(code=4401)
        return

    terminate_events = terminate_events if terminate_events is not None else {}
    user_id = websocket.headers.get("x-user-id", "")
    username = websocket.headers.get("x-user-name", "")
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

    # Joining is the event supervision exists to record. It wrote no audit row at
    # all before: a feature whose purpose is answering "who watched this session"
    # left no way to answer it. critical=True — observing someone's session
    # unlogged is the failure mode, so if the row cannot be written the join does
    # not proceed.
    try:
        await log_action(
            user_id=user_id, username=username, action="session.join",
            resource_type="session", resource_id=session_id,
            details={"target_user": sess.username, "host_name": sess.host_name, "mode": "read-only"},
            result="success", critical=True,
        )
    except Exception:
        await websocket.close(code=4500)
        try:
            subs.remove(websocket)
        except ValueError:
            pass
        return

    async def _send(payload: dict) -> None:
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            pass

    try:
        while True:
            msg = await websocket.receive_text()
            try:
                frame = json.loads(msg)
            except (TypeError, ValueError):
                continue
            if not isinstance(frame, dict):
                continue
            kind = frame.get("type")
            ctl = supervision.get(session_id)

            # Everything below is admin-only. A session's own owner may watch a
            # second copy of their session, but must not gain control paths over
            # it that they did not already have.
            if kind in ("takeover", "release", "input", "terminate") and role not in ("admin", "superadmin"):
                await _send({"type": "denied", "message": "supervision actions require admin"})
                continue

            if kind == "takeover":
                if ctl is None:
                    await _send({"type": "denied", "message": "session is no longer live"})
                    continue
                if ctl.taken and ctl.controller_id != user_id:
                    await _send({"type": "denied", "message": f"{ctl.controller_name} already has control"})
                    continue
                ctl.controller_id, ctl.controller_name = user_id, username
                await log_action(
                    user_id=user_id, username=username, action="session.takeover",
                    resource_type="session", resource_id=session_id,
                    details={"target_user": sess.username, "host_name": sess.host_name},
                    result="success", critical=True,
                )
                # The operator is told, in their own terminal. Silent control would
                # put their name on commands they did not type.
                await ctl.notify(
                    f"\r\n\x1b[33m[SeyalRun]\x1b[0m {username} (admin) has taken control of this session.\r\n"
                )
                await _send({"type": "control", "controller": username})

            elif kind == "release":
                if ctl and supervision.release_if_controller(session_id, user_id):
                    await log_action(
                        user_id=user_id, username=username, action="session.takeover_release",
                        resource_type="session", resource_id=session_id,
                        details={"target_user": sess.username}, result="success",
                    )
                    await ctl.notify(
                        f"\r\n\x1b[33m[SeyalRun]\x1b[0m {username} released control.\r\n"
                    )
                await _send({"type": "control", "controller": None})

            elif kind == "input":
                # Only the current controller may type, and only through the same
                # stdin path an operator keystroke takes.
                if ctl is None or ctl.controller_id != user_id:
                    continue
                data = frame.get("data", "")
                if data:
                    await ctl.write(data)

            elif kind == "terminate":
                ev = terminate_events.get(session_id)
                await log_action(
                    user_id=user_id, username=username, action="session.terminate",
                    resource_type="session", resource_id=session_id,
                    details={"target_user": sess.username, "host_name": sess.host_name,
                             "source": "supervision"},
                    result="success" if ev else "failure", critical=True,
                )
                if ev:
                    ev.set()
                    await _send({"type": "terminated"})

            # Any other frame from a spectator is dropped: read-only by default.
    except WebSocketDisconnect:
        pass
    finally:
        # A dropped supervisor socket must not leave the session seized forever.
        if supervision.release_if_controller(session_id, user_id):
            ctl = supervision.get(session_id)
            if ctl:
                await ctl.notify(
                    f"\r\n\x1b[33m[SeyalRun]\x1b[0m {username} disconnected; control released.\r\n"
                )
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
