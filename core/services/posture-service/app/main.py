"""posture-service: host facts + offline vulnerability-feed snapshots (SeyalRun 3.0 M1).

Reached only through api-gateway (segment ``posture``). It never opens SSH:
facts come from a fixed, read-only automation-service job, and feeds come from
signed bundles an admin uploads — there are no outbound internet calls.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from libs.obsmetrics import ServiceMetrics
from libs.securelog import configure_logging
from libs.servicetoken import ServiceTokenError, verify
from sqlalchemy import text

from . import audit, clients, collector, feeds
from .api.feeds import router as feeds_router
from .api.hosts import router as hosts_router
from .config import get_settings
from .database import engine

settings = get_settings()
configure_logging("posture-service", settings.log_level, settings.log_path)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    data = Path(settings.posture_data_dir)
    try:
        (data / "uploads").mkdir(parents=True, exist_ok=True)
        # Multipart uploads spool to tempfile.gettempdir(); keep them on the data
        # volume rather than the RAM-backed /tmp.
        os.environ.setdefault("TMPDIR", str(data / "uploads"))
        tempfile.tempdir = None  # re-read TMPDIR on next use
    except OSError:
        logger.warning("posture data dir %s is not writable; feed import will fail", data)
    try:
        feeds.trusted_keys()
    except Exception as exc:
        logger.error("POSTURE_FEED_TRUSTED_KEYS is invalid: %s", exc)
    try:
        await feeds.fail_interrupted_imports()
    except Exception:
        logger.exception("could not reconcile interrupted feed imports")
    poller = asyncio.create_task(collector.poll_loop())
    yield
    poller.cancel()
    try:
        await poller
    except asyncio.CancelledError:
        pass
    await clients.close()
    await engine.dispose()


app = FastAPI(title="posture-service", version="3.0.0", lifespan=lifespan)
_metrics = ServiceMetrics()
app.middleware("http")(_metrics.middleware)


# ── audit: one row per API call, reads included ──────────────────────────────
# (method, path, action, resource_type). The resource id is the path's capture.
_AUDIT_ROUTES: list[tuple[str, re.Pattern, str, str]] = [
    ("GET", re.compile(r"^/api/v1/posture/hosts/?$"), "posture.hosts.list", "host"),
    ("GET", re.compile(r"^/api/v1/posture/hosts/([^/]+)/?$"), "posture.host.view", "host"),
    ("POST", re.compile(r"^/api/v1/posture/hosts/([^/]+)/collect/?$"), "posture.host.collect", "host"),
    ("POST", re.compile(r"^/api/v1/posture/collect/?$"), "posture.collect_all", "host"),
    ("GET", re.compile(r"^/api/v1/posture/feeds/?$"), "posture.feeds.list", "feed_snapshot"),
    ("POST", re.compile(r"^/api/v1/posture/feeds/import/?$"), "posture.feed.import", "feed_snapshot"),
    ("POST", re.compile(r"^/api/v1/posture/feeds/([^/]+)/activate/?$"), "posture.feed.activate", "feed_snapshot"),
    ("GET", re.compile(r"^/api/v1/posture/summary/?$"), "posture.summary.view", ""),
]


def audit_action(method: str, path: str) -> tuple[str, str, str]:
    for m, rx, action, rtype in _AUDIT_ROUTES:
        if m == method:
            hit = rx.match(path)
            if hit:
                return action, rtype, (hit.group(1) if hit.groups() else "")[:36]
    return f"posture.api.{method.lower()}", "", ""


@app.middleware("http")
async def audit_every_call(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/v1/") or path.startswith("/api/v1/internal"):
        return await call_next(request)
    request.state.audit = {}
    request.state.audited = False
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        if not (request.state.audited and status_code < 400):
            # Identity headers are only trusted behind a valid service token —
            # otherwise anyone on the network could write rows in a user's name.
            user_id = username = None
            try:
                verify(request.headers.get("x-service-token", ""), "posture-service", settings.service_jwt_secret)
                user_id = request.headers.get("x-user-id") or None
                username = request.headers.get("x-user-name") or ""
            except ServiceTokenError:
                pass
            action, rtype, rid = audit_action(request.method, path)
            details = dict(request.state.audit.get("details") or {})
            details.update({"method": request.method, "path": path[:200], "status": status_code})
            if user_id is None:
                details["unauthenticated"] = True
            await audit.log_action(
                user_id=user_id, username=username or "", action=action, resource_type=rtype,
                resource_id=rid, details=details,
                ip_address=(request.headers.get("x-real-ip") or (request.client.host if request.client else ""))[:64],
                result="success" if status_code < 400 else ("denied" if status_code in (401, 403) else "failure"),
            )


app.include_router(hosts_router, prefix="/api/v1")
app.include_router(feeds_router, prefix="/api/v1")


@app.get("/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok}


@app.get("/metrics")
async def metrics():
    return _metrics.snapshot()
