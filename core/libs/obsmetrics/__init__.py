"""Shared JSON service metrics: request/error counters + uptime.

Every FastAPI service mounts one ServiceMetrics instance: the middleware
counts requests and 5xx responses, and GET /metrics returns one flat JSON
object. The Zabbix monitor endpoint (and the template's JSONPath
preprocessing) consume this JSON directly — no Prometheus text anywhere.
"""

from __future__ import annotations

import time


class ServiceMetrics:
    """Process-local counters — services run single-worker uvicorn, so plain
    ints are safe (no cross-process aggregation needed)."""

    def __init__(self) -> None:
        self.started = time.time()
        self.requests_total = 0
        self.errors_total = 0

    async def middleware(self, request, call_next):
        self.requests_total += 1
        response = await call_next(request)
        if response.status_code >= 500:
            self.errors_total += 1
        return response

    def snapshot(self, **extra) -> dict:
        """The /metrics response body: the three core keys the Zabbix template
        slices out, plus any service-specific extras (ignored by the monitor)."""
        return {
            "requests_total": self.requests_total,
            "errors_total": self.errors_total,
            "uptime_seconds": round(time.time() - self.started, 2),
            **extra,
        }
