"""Background metric collectors — called by metrics router on cache miss,
and also by a periodic background task that proactively refreshes the cache.

Every collector is an async function that returns a plain dict (JSON-serialisable).
Timestamps are Unix epoch seconds (integers). Numeric values are numbers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx
import redis.asyncio as aioredis

from .config import get_settings

logger = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = "seyalrun:metrics:"


def _now() -> int:
    return int(time.time())


async def _svc_get(service_url: str, path: str, settings=None, timeout: float = 4.0) -> dict | list | None:
    """GET from an internal service URL. Returns None on any error."""
    if settings is None:
        settings = get_settings()
    from libs.servicetoken import mint
    audience = service_url.split(":")[1].lstrip("/")
    token = mint("api-gateway", audience, settings.service_jwt_secret)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{service_url}/api/v1{path}",
                headers={
                    "X-Service-Token": token,
                    "X-User-Id": "__system__",
                    "X-User-Role": "admin",
                },
            )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


# ── System metrics (container-level, no internal service calls) ─────────────

async def collect_system() -> dict:
    """CPU, memory, disk, uptime using /proc — no psutil dependency."""
    ts = _now()
    result: dict[str, Any] = {"timestamp": ts}

    # Uptime
    try:
        with open("/proc/uptime") as f:
            result["uptime_seconds"] = int(float(f.read().split()[0]))
    except Exception:
        result["uptime_seconds"] = -1

    # Memory
    try:
        mem: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
        total_kb = mem.get("MemTotal", 0)
        avail_kb = mem.get("MemAvailable", 0)
        used_kb = total_kb - avail_kb
        result["memory_total_mb"]  = round(total_kb / 1024)
        result["memory_used_mb"]   = round(used_kb / 1024)
        result["memory_free_mb"]   = round(avail_kb / 1024)
        result["memory_used_pct"]  = round(used_kb / total_kb * 100, 1) if total_kb else 0
    except Exception:
        result.update(memory_total_mb=-1, memory_used_mb=-1, memory_free_mb=-1, memory_used_pct=-1)

    # CPU (read twice, delta)
    def _cpu_times():
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            vals = list(map(int, line.split()[1:]))
            idle = vals[3]
            total = sum(vals)
            return idle, total
        except Exception:
            return 0, 1

    idle0, total0 = _cpu_times()
    await asyncio.sleep(0.1)
    idle1, total1 = _cpu_times()
    d_idle = idle1 - idle0
    d_total = total1 - total0
    result["cpu_used_pct"] = round((1 - d_idle / max(d_total, 1)) * 100, 1)

    # Disk (root /)
    try:
        st = os.statvfs("/")
        total_bytes = st.f_blocks * st.f_frsize
        free_bytes  = st.f_bfree  * st.f_frsize
        used_bytes  = total_bytes - free_bytes
        result["disk_total_gb"]  = round(total_bytes / 1024**3, 1)
        result["disk_used_gb"]   = round(used_bytes  / 1024**3, 1)
        result["disk_free_gb"]   = round(free_bytes  / 1024**3, 1)
        result["disk_used_pct"]  = round(used_bytes / max(total_bytes, 1) * 100, 1)
    except Exception:
        result.update(disk_total_gb=-1, disk_used_gb=-1, disk_free_gb=-1, disk_used_pct=-1)

    # Open FDs (best-effort inside a container)
    try:
        result["open_fds"] = len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except Exception:
        result["open_fds"] = -1

    return result


# ── Application counters ─────────────────────────────────────────────────────

async def collect_app() -> dict:
    settings = get_settings()
    ts = _now()

    users_data    = await _svc_get(settings.identity_service_url, "/users", settings)
    sessions_data = await _svc_get(settings.terminal_service_url, "/ssh/sessions", settings)

    users_total      = len(users_data) if isinstance(users_data, list) else -1
    users_active_24h = -1
    users_disabled   = -1
    if isinstance(users_data, list):
        cutoff = ts - 86400
        users_active_24h = sum(
            1 for u in users_data
            if isinstance(u.get("last_login_at"), str) and
            _ts(u["last_login_at"]) > cutoff
        )
        users_disabled = sum(1 for u in users_data if not u.get("is_active", True))

    sessions_all     = sessions_data if isinstance(sessions_data, list) else []
    sessions_active  = sum(1 for s in sessions_all if s.get("status") == "active")
    sessions_pending = sum(1 for s in sessions_all if s.get("status") == "pending")
    sessions_idle_5m = sum(
        1 for s in sessions_all
        if s.get("status") == "active" and _ts(s.get("started_at")) < ts - 300
    )

    return {
        "timestamp":            ts,
        "users_total":          users_total,
        "users_active_24h":     users_active_24h,
        "users_disabled":       users_disabled,
        "sessions_active":      sessions_active,
        "sessions_pending":     sessions_pending,
        "sessions_idle_over_5m": sessions_idle_5m,
        "executions_running":   -1,   # populated by automation-service if available
        "executions_queued":    -1,
    }


def _ts(s: str | None) -> int:
    """Parse ISO datetime → epoch seconds, return 0 on failure."""
    if not s:
        return 0
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt.tzinfo is None else int(dt.timestamp())
    except Exception:
        return 0


# ── Playbooks ────────────────────────────────────────────────────────────────

async def collect_playbooks() -> dict:
    settings = get_settings()
    ts = _now()
    templates = await _svc_get(settings.automation_service_url, "/job-templates", settings)
    if not isinstance(templates, list):
        return {"timestamp": ts, "available": False}

    ansible = [t for t in templates if t.get("action_type") == "ansible_playbook"]
    scripts = [t for t in templates if t.get("action_type") == "bash_script"]
    all_types = {}
    for t in templates:
        at = t.get("action_type", "unknown")
        all_types[at] = all_types.get(at, 0) + 1

    return {
        "timestamp":              ts,
        "playbooks_total":        len(ansible),
        "playbooks_enabled":      sum(1 for t in ansible if t.get("enabled")),
        "playbooks_quick_action": sum(1 for t in ansible if t.get("quick_action")),
        "scripts_total":          len(scripts),
        "templates_total":        len(templates),
        "by_action_type":         all_types,
    }


# ── Webhooks (LLD) ───────────────────────────────────────────────────────────

async def collect_webhooks() -> dict:
    settings = get_settings()
    ts = _now()
    bindings = await _svc_get(settings.zabbix_integration_service_url, "/trigger-bindings", settings)
    if not isinstance(bindings, list):
        return {"timestamp": ts, "available": False, "data": []}

    data = []
    for b in bindings:
        data.append({
            "{#INTEGRATION}":      b.get("name", ""),
            "integration_id":      b.get("id", ""),
            "enabled":             b.get("enabled", True),
            "calls_total":         b.get("calls_total", 0),
            "calls_success_1h":    b.get("calls_success_1h", 0),
            "calls_failed_1h":     b.get("calls_failed_1h", 0),
            "signature_failures_24h": b.get("signature_failures_24h", 0),
            "replay_rejects_24h":  b.get("replay_rejects_24h", 0),
            "last_call_ts":        _ts(b.get("last_call_at")),
            "secret_age_days":     b.get("secret_age_days", 0),
        })

    return {"timestamp": ts, "total": len(data), "data": data}


# ── Executions ───────────────────────────────────────────────────────────────

async def collect_executions() -> dict:
    settings = get_settings()
    ts = _now()
    runs = await _svc_get(settings.automation_service_url, "/job-runs", settings)
    if not isinstance(runs, list):
        return {"timestamp": ts, "available": False}

    now = ts
    w5m  = now - 300
    w1h  = now - 3600
    w24h = now - 86400

    running  = [r for r in runs if r.get("status") == "running"]
    queued   = [r for r in runs if r.get("status") in ("pending", "queued")]
    last_5m  = [r for r in runs if _ts(r.get("started_at")) > w5m]
    last_1h  = [r for r in runs if _ts(r.get("started_at")) > w1h]
    last_24h = [r for r in runs if _ts(r.get("started_at")) > w24h]

    def _counts(lst):
        return {
            "success": sum(1 for r in lst if r.get("status") == "success"),
            "failed":  sum(1 for r in lst if r.get("status") in ("failed", "error")),
            "total":   len(lst),
        }

    durations = []
    for r in runs:
        if r.get("started_at") and r.get("ended_at"):
            d = _ts(r["ended_at"]) - _ts(r["started_at"])
            if d > 0:
                durations.append(d)

    return {
        "timestamp":         ts,
        "executions_running": len(running),
        "executions_queued":  len(queued),
        "window_5m":          _counts(last_5m),
        "window_1h":          _counts(last_1h),
        "window_24h":         _counts(last_24h),
        "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else 0,
        "failed_rate_1h_pct": round(
            _counts(last_1h)["failed"] / max(_counts(last_1h)["total"], 1) * 100, 1
        ),
    }


# ── Housekeeping (LLD) ───────────────────────────────────────────────────────

_KNOWN_HK_JOBS = [
    "kill_inactive_sessions",
    "expire_stale_tokens",
    "purge_old_recordings",
    "purge_audit_logs",
    "rotate_zabbix_cache",
]


async def collect_housekeeping() -> dict:
    """Read housekeeping status from Redis keys written by each background task."""
    ts = _now()
    settings = get_settings()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    data = []
    try:
        for job in _KNOWN_HK_JOBS:
            key = f"seyalrun:hk:{job}"
            raw = await r.get(key)
            if raw:
                try:
                    entry = json.loads(raw)
                except Exception:
                    entry = {}
            else:
                entry = {}

            data.append({
                "{#JOB}":               job,
                "enabled":              entry.get("enabled", True),
                "last_run_ts":          entry.get("last_run_ts", 0),
                "last_status":          entry.get("last_status", "unknown"),
                "last_duration_ms":     entry.get("last_duration_ms", 0),
                "last_affected":        entry.get("last_affected", 0),
                "consecutive_failures": entry.get("consecutive_failures", 0),
            })
    finally:
        await r.aclose()

    return {"timestamp": ts, "data": data}


def record_hk_job(job_name: str, status: str, duration_ms: int, affected: int = 0) -> None:
    """Call from housekeeping tasks to persist their run result in Redis.
    Fire-and-forget — creates its own event loop slice."""
    async def _write():
        settings = get_settings()
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        key = f"seyalrun:hk:{job_name}"
        prev_raw = await r.get(key)
        prev = {}
        if prev_raw:
            try:
                prev = json.loads(prev_raw)
            except Exception:
                pass
        consec = prev.get("consecutive_failures", 0)
        if status == "ok":
            consec = 0
        else:
            consec += 1
        entry = {
            "enabled": True,
            "last_run_ts": _now(),
            "last_status": status,
            "last_duration_ms": duration_ms,
            "last_affected": affected,
            "consecutive_failures": consec,
        }
        await r.setex(key, 86400 * 7, json.dumps(entry))
        await r.aclose()

    asyncio.create_task(_write())


# ── Integrations (LLD) ───────────────────────────────────────────────────────

async def collect_integrations() -> dict:
    settings = get_settings()
    ts = _now()

    targets = [
        ("identity-service",  settings.identity_service_url,  "internal"),
        ("inventory-service", settings.inventory_service_url, "internal"),
        ("terminal-service",  settings.terminal_service_url,  "internal"),
        ("automation-service", settings.automation_service_url, "optional"),
        ("zabbix-integration", settings.zabbix_integration_service_url, "optional"),
    ]

    data = []
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, url, svc_type in targets:
            t0 = time.monotonic()
            try:
                resp = await client.get(f"{url}/health")
                reachable = resp.status_code == 200
                latency_ms = round((time.monotonic() - t0) * 1000)
            except Exception:
                reachable = False
                latency_ms = -1

            data.append({
                "{#INTEGRATION_NAME}": name,
                "type":                svc_type,
                "reachable":           reachable,
                "latency_ms":          latency_ms,
                "last_check_ts":       ts,
            })

    return {"timestamp": ts, "data": data}


# ── Queue ────────────────────────────────────────────────────────────────────

async def collect_queue() -> dict:
    settings = get_settings()
    ts = _now()
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    data = []
    try:
        # Standard Celery queue names; scan for known patterns
        queue_names = ["celery", "seyalrun.jobs", "seyalrun.housekeeping", "seyalrun.webhooks"]
        for q in queue_names:
            try:
                depth = await r.llen(q)
                data.append({
                    "{#QUEUE}":     q,
                    "pending":      depth,
                    "active":       0,
                    "failed":       0,
                })
            except Exception:
                pass
    finally:
        await r.aclose()

    return {"timestamp": ts, "data": data}


# ── Audit ────────────────────────────────────────────────────────────────────

async def collect_audit() -> dict:
    settings = get_settings()
    ts = _now()
    logs = await _svc_get(settings.identity_service_url, "/audit/logs?limit=500", settings)
    if not isinstance(logs, list):
        return {"timestamp": ts, "available": False}

    now = ts
    w1h  = now - 3600
    w24h = now - 86400

    last_1h  = [e for e in logs if _ts(e.get("created_at") or e.get("timestamp")) > w1h]
    last_24h = [e for e in logs if _ts(e.get("created_at") or e.get("timestamp")) > w24h]

    def _by_action(lst: list) -> dict:
        counts: dict[str, int] = {}
        for e in lst:
            a = e.get("action", "unknown")
            counts[a] = counts.get(a, 0) + 1
        return counts

    return {
        "timestamp":         ts,
        "events_1h":         len(last_1h),
        "events_24h":        len(last_24h),
        "by_action_1h":      _by_action(last_1h),
        "by_action_24h":     _by_action(last_24h),
        "error_events_1h":   sum(1 for e in last_1h  if "fail" in e.get("action", "").lower() or "error" in e.get("action", "").lower()),
        "error_events_24h":  sum(1 for e in last_24h if "fail" in e.get("action", "").lower() or "error" in e.get("action", "").lower()),
    }


# ── Full bundle ──────────────────────────────────────────────────────────────

async def collect_all() -> dict:
    """Collect all metrics concurrently and bundle into one response."""
    results = await asyncio.gather(
        collect_system(),
        collect_app(),
        collect_playbooks(),
        collect_webhooks(),
        collect_executions(),
        collect_housekeeping(),
        collect_integrations(),
        collect_queue(),
        collect_audit(),
        return_exceptions=True,
    )
    keys = ["system", "app", "playbooks", "webhooks", "executions", "housekeeping", "integrations", "queue", "audit"]
    bundle: dict = {"timestamp": _now()}
    for k, r in zip(keys, results):
        bundle[k] = r if not isinstance(r, Exception) else {"error": str(r)}
    return bundle


# ── Background refresh task ──────────────────────────────────────────────────

async def background_metric_refresh(redis_url: str, interval_seconds: int = 30) -> None:
    """Runs indefinitely. Refreshes all metric caches proactively so endpoints
    always hit cache rather than blocking on slow collectors."""
    r = aioredis.from_url(redis_url, decode_responses=True)
    logger.info("metric background refresh started (interval=%ds)", interval_seconds)

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            t0 = time.monotonic()
            bundle = await collect_all()
            pipe = r.pipeline()
            pipe.setex(_CACHE_KEY_PREFIX + "all", 60, json.dumps(bundle))
            # Also cache individual sections so per-endpoint calls are fast
            for section in ["system", "app", "playbooks", "webhooks", "executions", "housekeeping", "integrations", "queue", "audit"]:
                if section in bundle and not isinstance(bundle[section], Exception):
                    pipe.setex(_CACHE_KEY_PREFIX + section, 60, json.dumps(bundle[section]))
            await pipe.execute()
            elapsed = round((time.monotonic() - t0) * 1000)
            logger.debug("metric refresh done in %dms", elapsed)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("metric refresh error: %s", exc)

    await r.aclose()
    logger.info("metric background refresh stopped")
