from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text

from libs.obsmetrics import ServiceMetrics
from libs.securelog import configure_logging
from libs.pluginbase import describe_plugins, discover_plugins, IdentityProvider

from .api import access_review, audit, auth, authorizations, command_filters, integrations, internal, login_acls, mail_settings, settings as settings_api, tokens, users
from .config import get_settings
from .deps import require_service_token
from .database import engine
from .redis_client import redis_client

settings = get_settings()
configure_logging(settings.service_name, settings.log_level, settings.log_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-discover identity provider plugins (local + zabbix_sso).
    app.state.idp_plugins = discover_plugins("app.plugins.idp", IdentityProvider)
    yield


app = FastAPI(title="SeyalRun Identity Service", version="2.0.0", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(authorizations.router, prefix="/api/v1")
app.include_router(command_filters.router, prefix="/api/v1")
app.include_router(login_acls.router, prefix="/api/v1")
app.include_router(tokens.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(settings_api.router, prefix="/api/v1")
app.include_router(mail_settings.router, prefix="/api/v1")
app.include_router(internal.router, prefix="/api/v1")
app.include_router(access_review.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(integrations.internal_router, prefix="/api/v1")

_metrics = ServiceMetrics()
app.middleware("http")(_metrics.middleware)


@app.get("/health")
async def health():
    db_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    redis_ok = True
    try:
        await redis_client.ping()
    except Exception:
        redis_ok = False
    ok = db_ok and redis_ok
    return {"status": "ok" if ok else "degraded", "service": settings.service_name, "db": db_ok, "redis": redis_ok}


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
    out += describe_plugins("idp", getattr(app.state, "idp_plugins", {}) or {}, "identity-service")

    return {"service": "identity-service", "plugins": out}
