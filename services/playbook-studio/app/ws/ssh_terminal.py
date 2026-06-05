"""SSH terminal WebSocket — with idle session pool (connections survive browser navigation)."""

from __future__ import annotations

import asyncio
import datetime
import json
import time
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..services.ssh_pool import IdleSession, add_idle, expire_session, get_idle, remove_idle

log = structlog.get_logger(__name__)
settings = get_settings()
router = APIRouter(tags=["ssh"])

# Active WebSocket connections (session_id → asyncssh conn, for forced kill)
_active_connections: dict[str, Any] = {}


@router.websocket("/ws/ssh/{session_id}")
async def ssh_terminal(websocket: WebSocket, session_id: str, token: str = "") -> None:
    await websocket.accept()

    if not token:
        await _close(websocket, "Missing auth token")
        return

    # Token validation via Redis cache
    try:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        cached = await redis.get(f"jms_token:{token[:16]}")
        await redis.aclose()
        if not cached:
            await _close(websocket, "Invalid or expired token")
            return
    except Exception:
        pass

    from ..database import AsyncSessionLocal
    from ..models.domain import SSHSession

    try:
        sess_uuid = uuid.UUID(session_id)
    except ValueError:
        await _close(websocket, "Invalid session ID")
        return

    # ── Check if this is a resume of an idle session ──────────────────────────
    idle = get_idle(session_id)
    if idle:
        idle.cancel_expiry()
        remove_idle(session_id)
        log.info("ssh_session_resumed", session_id=session_id[:8])

        # Mark DB active again
        async with AsyncSessionLocal() as db:
            sess = await db.get(SSHSession, sess_uuid)
            if sess:
                sess.status = "active"
                await db.commit()

        # Send buffered output first
        if idle.buffer:
            await websocket.send_json(
                {
                    "type": "connected",
                    "data": f"[Resumed session — {len(idle.buffer)} frames replayed]\r\n",
                }
            )
            for frame in idle.buffer[-500:]:  # replay up to last 500 frames
                await websocket.send_json({"type": "output", "data": frame["d"]})
        else:
            await websocket.send_json({"type": "connected", "data": "[Session resumed]\r\n"})

        await _forward(
            websocket,
            session_id,
            sess_uuid,
            idle.conn,
            idle.process,
            idle.tunnel_conn,
            session_start=time.monotonic(),
            existing_recording=idle.buffer,
        )
        return

    # ── New connection ────────────────────────────────────────────────────────
    async with AsyncSessionLocal() as db:
        sess = await db.get(SSHSession, sess_uuid)
        if not sess:
            await _close(websocket, "Session not found")
            return
        if sess.status not in ("pending",):
            await _close(websocket, f"Session is {sess.status}")
            return

        creds = sess.recording if isinstance(sess.recording, dict) else {}
        host = sess.asset_address
        ssh_user = sess.ssh_username
        password = creds.get("password", "")
        gateway = creds.get("gateway")

        sess.status = "active"
        sess.started_at = datetime.datetime.utcnow()
        sess.recording = []
        await db.commit()

    try:
        import asyncssh

        connect_kwargs: dict[str, Any] = {
            "host": host,
            "username": ssh_user,
            "known_hosts": None,
            "login_timeout": 20,
            "connect_timeout": 15,
            "keepalive_interval": 30,
        }
        if password:
            connect_kwargs["password"] = password
            connect_kwargs["preferred_auth"] = "password"

        tunnel_conn = None
        if gateway and gateway.get("host"):
            gw_kw: dict[str, Any] = {
                "host": gateway["host"],
                "port": int(gateway.get("port", 22)),
                "username": gateway.get("user", ssh_user),
                "known_hosts": None,
                "connect_timeout": 10,
            }
            if gateway.get("password"):
                gw_kw["password"] = gateway["password"]
            tunnel_conn = await asyncssh.connect(**gw_kw)
            connect_kwargs["tunnel"] = tunnel_conn

        conn = await asyncssh.connect(**connect_kwargs)
        _active_connections[session_id] = conn

        process = await conn.create_process(
            term_type="xterm-256color",
            term_size=(80, 24),
            encoding=None,
        )

        await websocket.send_json(
            {
                "type": "connected",
                "data": f"Connected to {host} as {ssh_user}\r\n",
            }
        )
        log.info("ssh_connected", session_id=session_id[:8], host=host)

        await _forward(
            websocket,
            session_id,
            sess_uuid,
            conn,
            process,
            tunnel_conn,
            session_start=time.monotonic(),
        )

    except Exception as exc:
        log.error("ssh_error", session_id=session_id[:8], error=str(exc))
        try:
            await websocket.send_json({"type": "error", "data": f"SSH error: {exc}"})
        except Exception:
            pass
        async with AsyncSessionLocal() as db:
            sess = await db.get(SSHSession, sess_uuid)
            if sess:
                sess.status = "error"
                sess.exit_reason = str(exc)
                await db.commit()

    _active_connections.pop(session_id, None)


async def _forward(
    websocket: WebSocket,
    session_id: str,
    sess_uuid: uuid.UUID,
    conn: Any,
    process: Any,
    tunnel_conn: Any,
    session_start: float,
    existing_recording: list | None = None,
) -> None:
    """Bidirectional forwarding. On WS disconnect → move to idle pool."""
    from ..database import AsyncSessionLocal
    from ..models.domain import SSHSession

    recording: list[dict] = list(existing_recording or [])
    input_count = 0
    stop_event = asyncio.Event()

    async def read_ssh() -> None:
        try:
            while not stop_event.is_set():
                try:
                    chunk = await asyncio.wait_for(process.stdout.read(4096), timeout=0.1)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    t = round(time.monotonic() - session_start, 3)
                    recording.append({"t": t, "d": text})
                    await websocket.send_json({"type": "output", "data": text})
                except TimeoutError:
                    continue
                except Exception:
                    break
        finally:
            stop_event.set()

    async def read_ws() -> None:
        nonlocal input_count
        try:
            while not stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                except TimeoutError:
                    try:
                        await websocket.send_json({"type": "ping"})
                    except Exception:
                        break
                    continue
                except (WebSocketDisconnect, Exception):
                    break

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") == "input":
                    data = msg.get("data", "")
                    if data:
                        process.stdin.write(data.encode("utf-8"))
                        await process.stdin.drain()
                        input_count += 1
                elif msg.get("type") == "resize":
                    try:
                        process.change_terminal_size(
                            max(10, int(msg.get("cols", 80))),
                            max(2, int(msg.get("rows", 24))),
                        )
                    except Exception:
                        pass
                elif msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        finally:
            stop_event.set()

    ssh_task = asyncio.create_task(read_ssh())
    ws_task = asyncio.create_task(read_ws())
    done, pending = await asyncio.wait([ssh_task, ws_task], return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()

    ws_closed = ws_task in done  # True = browser navigated away
    ssh_closed = ssh_task in done  # True = remote SSH ended

    if ws_closed and not ssh_closed:
        # Browser disconnected — move to idle pool instead of closing
        from ..services.ssh_pool import get_idle_timeout_seconds

        timeout_mins = round(get_idle_timeout_seconds() / 60)
        log.info("ssh_going_idle", session_id=session_id[:8], timeout_mins=timeout_mins)

        idle = IdleSession(session_id, conn, process, tunnel_conn, list(recording))
        idle.start_buffering(session_start)
        idle.schedule_expiry(expire_session)
        add_idle(session_id, idle)
        _active_connections.pop(session_id, None)

        # Update DB to idle
        async with AsyncSessionLocal() as db:
            sess = await db.get(SSHSession, sess_uuid)
            if sess:
                sess.status = "idle"
                sess.recording = recording[-500:]
                await db.commit()

        log.info("ssh_idle_started", session_id=session_id[:8])
        return

    # SSH ended or explicit close — terminate fully
    _active_connections.pop(session_id, None)
    duration = round(time.monotonic() - session_start, 1)

    try:
        process.close()
        conn.close()
        if tunnel_conn:
            tunnel_conn.close()
    except Exception:
        pass

    try:
        await websocket.send_json({"type": "closed", "reason": "Session ended"})
    except Exception:
        pass

    async with AsyncSessionLocal() as db:
        sess = await db.get(SSHSession, sess_uuid)
        if sess:
            sess.status = "closed"
            sess.ended_at = datetime.datetime.utcnow()
            sess.duration_seconds = duration
            sess.recording = recording[-5000:]
            sess.command_count = input_count
            await db.commit()

    log.info("ssh_closed", session_id=session_id[:8], duration=duration)


async def _close(ws: WebSocket, reason: str) -> None:
    try:
        await ws.send_json({"type": "error", "data": reason})
        await ws.close()
    except Exception:
        pass


def terminate_session(session_id: str) -> bool:
    from ..services.ssh_pool import get_idle, remove_idle

    idle = get_idle(session_id)
    if idle:
        idle.close()
        remove_idle(session_id)
        return True
    conn = _active_connections.get(session_id)
    if conn:
        conn.close()
        return True
    return False
