from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from libs.obsmetrics import ServiceMetrics
from libs.securelog import configure_logging

from .config import get_settings
from .database import engine
from .api.webhook import router as webhook_router
from .api.trigger_bindings import router as bindings_router
from .api.monitor import router as monitor_router

_settings = get_settings()
configure_logging("zabbix-integration-service", _settings.log_level, _settings.log_path)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="zabbix-integration-service", version="2.0.0", lifespan=lifespan)
_metrics = ServiceMetrics()
app.middleware("http")(_metrics.middleware)

app.include_router(webhook_router, prefix="/api/v1")
app.include_router(bindings_router, prefix="/api/v1")
app.include_router(monitor_router, prefix="/api/v1")


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
