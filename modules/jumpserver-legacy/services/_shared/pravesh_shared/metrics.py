"""Prometheus metrics helpers — imported by all Pravesh services."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, make_asgi_app, start_http_server

# Standard metrics every sidecar exposes
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

EXTERNAL_API_ERRORS = Counter(
    "external_api_errors_total",
    "Errors calling external APIs",
    ["target", "error_type"],
)

CIRCUIT_BREAKER_STATE = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state: 0=closed (healthy), 1=open (failing), 0.5=half-open",
    ["target"],
)

JOB_COUNT = Counter(
    "jobs_total",
    "Total background jobs executed",
    ["job_type", "status"],
)

JOB_DURATION = Histogram(
    "job_duration_seconds",
    "Background job duration",
    ["job_type"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)


def make_metrics_app():
    """Return a WSGI/ASGI app that serves Prometheus metrics on /metrics."""
    return make_asgi_app()


def start_metrics_server(port: int = 9090) -> None:
    """Start a standalone metrics HTTP server (for non-FastAPI services)."""
    start_http_server(port)
