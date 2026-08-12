"""automation-bridge FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from pravesh_shared.health import router as health_router
from pravesh_shared.log import configure_logging
from pravesh_shared.middleware import RequestContextMiddleware
from prometheus_client import make_asgi_app
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .api.ansible import init_resources
from .api.ansible import router as ansible_router
from .config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()
configure_logging(settings.service_name, settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("automation_bridge_starting", port=settings.port)
    init_resources(settings)
    yield
    log.info("automation_bridge_stopping")


limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="automation-bridge",
    description="Run Ansible and Salt-SSH jobs from JumpServer UI",
    version="0.1.0",
    docs_url="/docs",  # Disable in prod: docs_url=None
    redoc_url=None,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(RequestContextMiddleware)

# Routes
app.include_router(health_router)
app.include_router(ansible_router)

# Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
