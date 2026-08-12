"""Pure helpers for the agentless Zabbix monitor endpoint.

Every service's /metrics is JSON (libs/obsmetrics), so parsing here is a
JSON key-pick with clean-zero fallbacks. Kept stdlib-only (no fastapi/httpx
at import time) so the security-invariant suite in tests/ can load this file
directly by path, same as the other dependency-light modules it covers.
"""

from __future__ import annotations

import json

METRIC_NAMES = ("requests_total", "errors_total", "uptime_seconds")


def classify_health(status_code: int | None, body: str) -> str:
    """Map a /health response to "ok" | "degraded" | "down".

    Same semantics the zabbix-agent2 sidecar's health.sh had: prefer the JSON
    "status" field, fall back to the bare body text for plain-text health
    endpoints (edge-proxy/frontend), and report anything unreachable or
    non-200 as "down".
    """
    if status_code != 200:
        return "down"
    try:
        status = json.loads(body).get("status", "")
        if status:
            return str(status)
    except (ValueError, AttributeError):
        pass
    text = "".join(body.split()).strip('"')
    return text or "down"


def parse_seyalrun_metrics(body: str) -> dict[str, float]:
    """Extract the core values from a JSON /metrics body (libs/obsmetrics
    snapshot shape: {"requests_total": N, "errors_total": N,
    "uptime_seconds": N, ...extras}).

    A missing key, a non-numeric value, or an unparsable body yields 0 —
    triggers see a clean zero, not "down". Service-specific extra keys
    (e.g. api-gateway's "upstreams") are ignored.
    """
    values = dict.fromkeys(METRIC_NAMES, 0.0)
    try:
        doc = json.loads(body)
    except ValueError:
        return values
    if not isinstance(doc, dict):
        return values
    for name in METRIC_NAMES:
        v = doc.get(name)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            values[name] = float(v)
    return values
