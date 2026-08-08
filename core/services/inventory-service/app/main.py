from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import text

from libs.obsmetrics import ServiceMetrics
from libs.securelog import configure_logging
from libs.pluginbase import discover_plugins, CredentialKind

from .api import credentials, hosts, log_backend, rotation, zones
from .config import get_settings
from .database import engine

settings = get_settings()
configure_logging(settings.service_name, settings.log_level, settings.log_path)

app = FastAPI(title="SeyalRun Inventory Service", version="2.0.0")

app.include_router(hosts.router, prefix="/api/v1")
app.include_router(credentials.router, prefix="/api/v1")
app.include_router(rotation.router, prefix="/api/v1")
app.include_router(log_backend.router, prefix="/api/v1")
app.include_router(zones.router, prefix="/api/v1")

_metrics = ServiceMetrics()
app.middleware("http")(_metrics.middleware)


@app.on_event("startup")
async def _startup() -> None:
    import asyncio

    from .log_shipper import run_shipper

    # Auto-discover credential kind plugins (password, ssh_key, vault_path).
    app.state.credential_kinds = discover_plugins("app.plugins.credentials", CredentialKind)
    # Log shipper: tails the shared seyalrun_logs volume and ships to the configured
    # backend (ES/S3). No-op while backend == local.
    app.state.log_shipper_task = asyncio.create_task(run_shipper())


@app.get("/health")
async def health():
    db_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "service": settings.service_name, "db": db_ok}


@app.get("/metrics")
async def metrics():
    return _metrics.snapshot()
