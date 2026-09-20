from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text

from libs.obsmetrics import ServiceMetrics
from libs.pluginbase import TriggerSource, describe_plugins, discover_plugins
from libs.securelog import configure_logging

from .config import get_settings
from .deps import require_service_token
from .database import engine
from .api.webhook import router as webhook_router
from .api.trigger_bindings import router as bindings_router
from .api.monitor import router as monitor_router

_settings = get_settings()
configure_logging("zabbix-integration-service", _settings.log_level, _settings.log_path)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.trigger_sources = discover_plugins("app.plugins.triggers", TriggerSource)
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


# ── Integration page: what plugins did this service actually load? ───────────
# Reports the live discovery registry rather than a hand-kept list, so a plugin
# that failed to import cannot show up here as present. api-gateway fans this
# out across services for GET /api/v1/integrations/plugins.
@app.get("/api/v1/internal/plugins", dependencies=[Depends(require_service_token)])
async def internal_plugins():
    out = []
    out += describe_plugins("triggers", getattr(app.state, "trigger_sources", {}) or {}, "zabbix-integration-service")

    return {"service": "zabbix-integration-service", "plugins": out}
