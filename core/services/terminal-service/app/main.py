from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket
from sqlalchemy import text

from libs.obsmetrics import ServiceMetrics
from libs.pluginbase import CommandFilterMatcher, discover_plugins
from libs.securelog import configure_logging

from .config import get_settings
from .database import engine
from .api.sessions import router as sessions_router
from .api.internal import router as internal_router

_settings = get_settings()
configure_logging("terminal-service", _settings.log_level, _settings.log_path)
logger = logging.getLogger(__name__)

# Terminate-event registry: session_id → asyncio.Event
# The DELETE /ssh/sessions/{id} endpoint sets the event;
# the WS handler awaits it.
_terminate_events: dict[str, asyncio.Event] = {}

# Live session viewer (PCI DSS Phase D): session_id → list of spectator
# WebSockets, tee'd a copy of the primary session's output (see
# ws/terminal.py::TeeWebSocket). In-process only — horizontal scaling of this
# service would need Redis pub/sub instead, since a spectator connecting to a
# different instance than the primary session wouldn't see this dict.
_spectators: dict[str, list[WebSocket]] = {}


async def _reap_pending_loop() -> None:
    """Mark sessions that were created but never had a WS connect (stuck 'pending')
    as 'closed' after pending_reap_seconds, so abandoned/test sessions don't pile up."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from .database import SessionLocal
    from .models import ZASSHSession

    settings = get_settings()
    while True:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.pending_reap_seconds)
            async with SessionLocal() as s:
                await s.execute(
                    update(ZASSHSession)
                    .where(ZASSHSession.status == "pending", ZASSHSession.started_at < cutoff)
                    .values(status="closed", ended_at=datetime.now(timezone.utc),
                            error_message="abandoned — never connected")
                )
                await s.commit()
        except Exception:
            logger.exception("pending-session reaper error")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.filter_matchers: dict[str, Any] = discover_plugins(
        "app.plugins.command_filters", CommandFilterMatcher
    )
    logger.info("command filter matchers loaded", extra={"matchers": list(app.state.filter_matchers.keys())})
    reaper = asyncio.create_task(_reap_pending_loop())
    yield
    reaper.cancel()
    try:
        await reaper
    except asyncio.CancelledError:
        pass
    await engine.dispose()


app = FastAPI(title="terminal-service", version="2.0.0", lifespan=lifespan)
_metrics = ServiceMetrics()
app.middleware("http")(_metrics.middleware)

app.include_router(sessions_router, prefix="/api/v1")
app.include_router(internal_router, prefix="/api/v1")


@app.websocket("/ws/ssh/{session_id}")
async def ws_ssh_terminal(websocket: WebSocket, session_id: str):
    from .ws.terminal import handle_terminal, TeeWebSocket
    tee = TeeWebSocket(websocket, session_id, _spectators)
    await handle_terminal(tee, session_id, _terminate_events)


@app.websocket("/ws/ssh/{session_id}/spectate")
async def ws_ssh_spectate(websocket: WebSocket, session_id: str):
    from .ws.spectate import handle_spectate
    await handle_spectate(websocket, session_id, _spectators)


@app.get("/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    status = "ok" if db_ok else "degraded"
    return {"status": status, "db": db_ok}


@app.get("/metrics")
async def metrics():
    return _metrics.snapshot()
