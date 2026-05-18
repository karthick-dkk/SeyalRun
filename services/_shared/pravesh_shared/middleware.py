"""FastAPI middleware: request ID injection, metrics, security headers."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

import structlog
from fastapi import Request, Response
from fastapi.middleware.base import BaseHTTPMiddleware

from .metrics import REQUEST_COUNT, REQUEST_LATENCY

log = structlog.get_logger(__name__)

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Server": "Pravesh",  # Obscure actual server type
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Injects trace_id into structlog context and records Prometheus metrics."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
        )

        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            REQUEST_COUNT.labels(method=request.method, path=request.url.path, status=500).inc()
            raise
        finally:
            duration = time.perf_counter() - start
            REQUEST_LATENCY.labels(method=request.method, path=request.url.path).observe(duration)

        REQUEST_COUNT.labels(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        ).inc()

        response.headers["X-Trace-Id"] = trace_id
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value

        return response
