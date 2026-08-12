from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from sqlalchemy import text

from libs.obsmetrics import ServiceMetrics
from libs.pluginbase import ActionExecutor, discover_plugins
from libs.securelog import configure_logging

from .config import get_settings
from .database import engine
from .api.projects import router as projects_router
from .api.job_templates import router as templates_router
from .api.schedules import router as schedules_router
from .api.job_runs import router as runs_router
from .api.internal import router as internal_router
from .api.housekeeping import router as housekeeping_router
from .api.test_connection import router as test_connection_router
from .api.notifications import router as notifications_router

_settings = get_settings()
configure_logging("automation-service", _settings.log_level, _settings.log_path)
logger = logging.getLogger(__name__)


_DEFAULT_TEMPLATES = [
    ("Default · Create / Push Account", "account_push"),
    ("Default · Rotate Secret", "rotate_secret"),
    ("Default · Disable Account", "disable_account"),
    ("Default · Remove Account", "remove_account"),
]


async def _seed_default_templates() -> None:
    """Idempotently ensure built-in account-lifecycle job templates exist so the Assets
    panel's account operations work out of the box (credential resolved per host)."""
    from sqlalchemy import select
    from .database import SessionLocal
    from .models import ZAProject, ZAJobTemplate

    async with SessionLocal() as s:
        res = await s.execute(select(ZAProject).where(ZAProject.name == "Account Operations"))
        proj = res.scalar_one_or_none()
        if proj is None:
            proj = ZAProject(name="Account Operations", description="Built-in account lifecycle operations", source_type="local")
            s.add(proj)
            await s.flush()
        for name, action_type in _DEFAULT_TEMPLATES:
            ex = await s.execute(select(ZAJobTemplate).where(ZAJobTemplate.name == name))
            if ex.scalar_one_or_none() is None:
                s.add(ZAJobTemplate(project_id=proj.id, name=name, action_type=action_type,
                                    target_scope="hosts", enabled=True))
        await s.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.executors = discover_plugins("app.plugins.executors", ActionExecutor)
    logger.info("executors loaded", extra={"executors": list(app.state.executors.keys())})

    try:
        await _seed_default_templates()
    except Exception:
        logger.exception("default template seed failed")

    from .scheduler import scheduler_loop
    sched_task = asyncio.create_task(scheduler_loop(app.state.executors))

    from .housekeeping import housekeeping_loop
    hk_task = asyncio.create_task(housekeeping_loop())

    yield

    for task in (sched_task, hk_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(title="automation-service", version="2.0.0", lifespan=lifespan)
_metrics = ServiceMetrics()
app.middleware("http")(_metrics.middleware)

app.include_router(projects_router, prefix="/api/v1")
app.include_router(templates_router, prefix="/api/v1")
app.include_router(schedules_router, prefix="/api/v1")
app.include_router(runs_router, prefix="/api/v1")
app.include_router(internal_router, prefix="/api/v1")
app.include_router(housekeeping_router, prefix="/api/v1")
app.include_router(test_connection_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")


@app.websocket("/ws/jobs/{run_id}/log")
async def _ws_job_log(websocket: WebSocket, run_id: str):
    # App-level (no /api/v1 prefix, no router service-token dep) so the path matches the
    # gateway WS proxy — same pattern as terminal-service's /ws/ssh route.
    from .api.job_runs import ws_job_log
    await ws_job_log(websocket, run_id)


@app.websocket("/ws/notifications")
async def _ws_notifications(websocket: WebSocket):
    from .api.notifications import ws_notifications
    await ws_notifications(websocket)


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
