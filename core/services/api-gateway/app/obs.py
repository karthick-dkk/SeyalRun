"""In-process per-upstream response-time tracking.

Every API call is proxied through the gateway to a named upstream service, so recording
latency here gives "all module response times" in one place — exposed as JSON on both
/metrics (the "upstreams" key) and /api/metrics/response-times (Zabbix HTTP-agent LLD)."""

from __future__ import annotations

from collections import defaultdict, deque

# service -> rolling window of recent durations in ms (last 500 requests)
_SAMPLES: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
_TOTAL: dict[str, int] = defaultdict(int)
_ERRORS: dict[str, int] = defaultdict(int)


def record(service: str, ms: float, is_error: bool = False) -> None:
    _SAMPLES[service].append(ms)
    _TOTAL[service] += 1
    if is_error:
        _ERRORS[service] += 1


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(round((pct / 100.0) * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def snapshot() -> dict[str, dict]:
    """Per-service {count, errors, avg_ms, p50_ms, p95_ms, max_ms} over the window."""
    out: dict[str, dict] = {}
    for svc, samples in _SAMPLES.items():
        if not samples:
            continue
        arr = sorted(samples)
        n = len(arr)
        out[svc] = {
            "count": _TOTAL[svc],
            "errors": _ERRORS[svc],
            "avg_ms": round(sum(arr) / n, 1),
            "p50_ms": round(_percentile(arr, 50), 1),
            "p95_ms": round(_percentile(arr, 95), 1),
            "max_ms": round(arr[-1], 1),
            "window": n,
        }
    return out
