"""SSH session idle pool — keeps connections alive after browser disconnects."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ── Settings (persisted to /tmp/seyalrun_settings.json) ──────────────────────

_SETTINGS_FILE = "/tmp/seyalrun_settings.json"
_DEFAULT_SETTINGS = {
    "ssh_idle_timeout_minutes": 15,
}


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_FILE) as f:
            return {**_DEFAULT_SETTINGS, **json.load(f)}
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def _save_settings(s: dict) -> None:
    try:
        with open(_SETTINGS_FILE, "w") as f:
            json.dump(s, f)
    except Exception:
        pass


_settings: dict = _load_settings()


def get_settings_all() -> dict:
    return dict(_settings)


def update_settings(patch: dict) -> dict:
    _settings.update(patch)
    _save_settings(_settings)
    return dict(_settings)


def get_idle_timeout_seconds() -> float:
    return float(_settings.get("ssh_idle_timeout_minutes", 15)) * 60


# ── Idle session pool ─────────────────────────────────────────────────────────


class IdleSession:
    def __init__(
        self,
        session_id: str,
        conn: Any,
        process: Any,
        tunnel_conn: Any,
        buffer: list[dict],
    ):
        self.session_id = session_id
        self.conn = conn
        self.process = process
        self.tunnel_conn = tunnel_conn
        self.buffer = buffer  # [{t, d}] output frames while idle
        self.disconnected_at = time.monotonic()
        self._timer_task: asyncio.Task | None = None
        self._buffer_task: asyncio.Task | None = None
        self._active = True  # still running

    def time_idle(self) -> float:
        return time.monotonic() - self.disconnected_at

    def start_buffering(self, session_start: float) -> None:
        """Continue reading SSH stdout into buffer while idle."""
        self._buffer_task = asyncio.create_task(self._buffer_loop(session_start))

    async def _buffer_loop(self, session_start: float) -> None:
        try:
            while self._active:
                try:
                    chunk = await asyncio.wait_for(self.process.stdout.read(4096), timeout=0.2)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    t = round(time.monotonic() - session_start, 3)
                    self.buffer.append({"t": t, "d": text})
                    # Cap buffer at 2000 frames to avoid memory growth
                    if len(self.buffer) > 2000:
                        self.buffer = self.buffer[-2000:]
                except TimeoutError:
                    continue
                except Exception:
                    break
        except Exception:
            pass

    def schedule_expiry(self, on_expire) -> None:
        timeout = get_idle_timeout_seconds()
        self._timer_task = asyncio.create_task(self._expire(timeout, on_expire))

    async def _expire(self, timeout: float, on_expire) -> None:
        await asyncio.sleep(timeout)
        log.info("ssh_idle_expired", session_id=self.session_id[:8])
        await on_expire(self.session_id)

    def cancel_expiry(self) -> None:
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        if self._buffer_task and not self._buffer_task.done():
            self._buffer_task.cancel()

    def close(self) -> None:
        self._active = False
        self.cancel_expiry()
        try:
            self.process.close()
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass
        if self.tunnel_conn:
            try:
                self.tunnel_conn.close()
            except Exception:
                pass


# ── Global pool ────────────────────────────────────────────────────────────────

_pool: dict[str, IdleSession] = {}


def add_idle(session_id: str, idle: IdleSession) -> None:
    _pool[session_id] = idle


def get_idle(session_id: str) -> IdleSession | None:
    return _pool.get(session_id)


def remove_idle(session_id: str) -> IdleSession | None:
    return _pool.pop(session_id, None)


def list_idle() -> list[dict]:
    return [
        {
            "session_id": sid,
            "idle_seconds": round(s.time_idle()),
            "buffer_frames": len(s.buffer),
        }
        for sid, s in _pool.items()
    ]


async def expire_session(session_id: str) -> None:
    """Called when idle timer fires — close SSH + update DB."""
    idle = _pool.pop(session_id, None)
    if not idle:
        return
    idle.close()

    # Update DB
    try:
        import datetime
        import uuid as _uuid

        from ..database import AsyncSessionLocal
        from ..models.domain import SSHSession

        async with AsyncSessionLocal() as db:
            sess = await db.get(SSHSession, _uuid.UUID(session_id))
            if sess and sess.status == "idle":
                sess.status = "closed"
                sess.exit_reason = "Idle timeout"
                sess.ended_at = datetime.datetime.utcnow()
                sess.recording = idle.buffer[-5000:]
                await db.commit()
    except Exception as exc:
        log.warning("idle_expire_db_error", error=str(exc))

    log.info("ssh_idle_closed", session_id=session_id[:8])
