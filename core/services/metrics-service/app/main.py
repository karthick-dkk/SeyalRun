"""metrics-service: read-only dashboard aggregator across all four SeyalRun databases."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from libs.obsmetrics import ServiceMetrics
from libs.dbcore import build_database_url
from libs.securelog import configure_logging
from libs.servicetoken import verify

from .config import get_settings

settings = get_settings()
configure_logging("metrics-service", settings.log_level, settings.log_path)
logger = logging.getLogger(__name__)


def _make_engine(db_name: str):
    url = build_database_url(
        settings.db_engine, settings.db_user, settings.db_password,
        settings.db_host, settings.db_port, db_name,
    )
    from libs.dbcore import make_engine, make_sessionmaker
    engine = make_engine(url, settings.db_engine, settings.db_sslmode)
    return make_sessionmaker(engine)


_sessions: dict = {}


def _get_session(db: str):
    if db not in _sessions:
        _sessions[db] = _make_engine(db)
    return _sessions[db]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prewarm all four DB pools so the first dashboard call isn't a ~400ms cold start.
    for db in (settings.identity_db_name, settings.inventory_db_name,
               settings.terminal_db_name, settings.automation_db_name):
        try:
            async with _get_session(db)() as s:
                await s.execute(text("SELECT 1"))
        except Exception as e:  # noqa: BLE001 - prewarm is best-effort; a missing/
            # unready DB (e.g. asyncpg InvalidCatalogNameError, which is NOT a
            # SQLAlchemyError) must never crash startup — the dashboard degrades per panel.
            logger.warning("prewarm %s failed: %s", db, e)
    yield


app = FastAPI(title="metrics-service", version="2.0.0", lifespan=lifespan)
_metrics = ServiceMetrics()
app.middleware("http")(_metrics.middleware)

# E2: dashboard payload cache. The dashboard is global (not per-user), so a single
# short-TTL cache serves every caller and avoids ~15 cross-DB queries per load.
_dash_cache: dict = {"ts": 0.0, "data": None}


def _require_service_token(x_service_token: str = Header(default="", alias="X-Service-Token")):
    # default="" (not required) so a MISSING token returns 401, not FastAPI's 422
    # validation error — consistent with the other services' deps.
    if not x_service_token:
        raise HTTPException(status_code=401, detail="missing service token")
    try:
        return verify(x_service_token, "metrics-service", settings.service_jwt_secret)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid service token")


@app.get("/api/v1/metrics/dashboard")
async def dashboard(token: dict = Depends(_require_service_token)):
    ttl = settings.dashboard_cache_ttl_seconds
    if _dash_cache["data"] is not None and (time.monotonic() - _dash_cache["ts"]) < ttl:
        return _dash_cache["data"]

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    # Per-panel degraded tracking (D-errors): a DB outage marks the affected panel
    # degraded instead of returning a fake zero that looks like real "all quiet" data.
    result: dict = {"degraded": []}

    # Hosts + credentials from inventory DB
    try:
        async with _get_session(settings.inventory_db_name)() as s:
            total = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_hosts"))).scalar() or 0
            enabled = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_hosts WHERE enabled = true"))).scalar() or 0
            reachable = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_hosts WHERE is_reachable = true"))).scalar() or 0
            result["hosts"] = {"total": total, "enabled": enabled, "reachable": reachable}

            cred_total = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_credentials"))).scalar() or 0
            weak = (await s.execute(
                text("SELECT COUNT(*) FROM v_metrics_credentials WHERE strength_score IS NOT NULL AND strength_score < :t"),
                {"t": settings.weak_credential_threshold},
            )).scalar() or 0
            rotation_due = (await s.execute(
                text("SELECT COUNT(*) FROM v_metrics_rotation_policies WHERE enabled = true AND next_rotation_due IS NOT NULL AND next_rotation_due <= :now"),
                {"now": now},
            )).scalar() or 0
            result["credentials"] = {"total": cred_total, "weak": weak, "rotation_due": rotation_due}
    except (SQLAlchemyError, OSError) as e:
        logger.warning("inventory metrics degraded: %s", e)
        result["degraded"].append("inventory")
        result["hosts"] = {"total": 0, "enabled": 0, "reachable": 0, "degraded": True}
        result["credentials"] = {"total": 0, "weak": 0, "rotation_due": 0, "degraded": True}
    except Exception as e:  # noqa: BLE001 - last resort, still marks degraded (never silent)
        logger.warning("inventory metrics error: %s", e)
        result["degraded"].append("inventory")
        result["hosts"] = {"total": 0, "enabled": 0, "reachable": 0, "degraded": True}
        result["credentials"] = {"total": 0, "weak": 0, "rotation_due": 0, "degraded": True}

    # Sessions + ACL blocks from terminal DB
    try:
        async with _get_session(settings.terminal_db_name)() as s:
            active = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_ssh_sessions WHERE status = 'active'"))).scalar() or 0
            last24h = (await s.execute(
                text("SELECT COUNT(*) FROM v_metrics_ssh_sessions WHERE started_at >= :cutoff"),
                {"cutoff": cutoff_24h},
            )).scalar() or 0
            failed24h = (await s.execute(
                text("SELECT COUNT(*) FROM v_metrics_ssh_sessions WHERE status IN ('error','terminated') AND started_at >= :cutoff"),
                {"cutoff": cutoff_24h},
            )).scalar() or 0
            result["sessions"] = {"active": active, "last_24h": last24h, "failed_24h": failed24h}

            try:
                rows = (await s.execute(text(
                    "SELECT c.command_text, c.executed_at, s.username, s.host_name "
                    "FROM v_metrics_session_commands c JOIN v_metrics_ssh_sessions s ON s.id = c.session_id "
                    "WHERE c.action IN ('deny','denied_by_default') ORDER BY c.executed_at DESC LIMIT 10"
                ))).fetchall()
                result["acl_blocks"] = [
                    {"command": r[0], "ts": r[1].isoformat() if r[1] else None, "user": r[2], "host": r[3]}
                    for r in rows
                ]
            except (SQLAlchemyError, OSError):
                result["acl_blocks"] = []
    except Exception as e:  # noqa: BLE001 - marks degraded, never a silent fake zero
        logger.warning("sessions metrics degraded: %s", e)
        result["degraded"].append("sessions")
        result["sessions"] = {"active": 0, "last_24h": 0, "failed_24h": 0, "degraded": True}
        result["acl_blocks"] = []

    # Job runs from automation DB
    try:
        async with _get_session(settings.automation_db_name)() as s:
            for st in ("success", "failed", "running"):
                count = (await s.execute(
                    text("SELECT COUNT(*) FROM v_metrics_job_runs WHERE status = :st AND started_at >= :cutoff"),
                    {"st": st, "cutoff": cutoff_24h},
                )).scalar() or 0
                result.setdefault("jobs", {}).setdefault("last_24h", {})[st] = count
            # Upcoming schedules
            rows = (await s.execute(
                text("SELECT name, next_run_at FROM v_metrics_schedules WHERE enabled = true ORDER BY next_run_at LIMIT 5")
            )).fetchall()
            result.setdefault("jobs", {})
            result["schedules"] = {"upcoming": [{"name": r[0], "next_run_at": r[1].isoformat() if r[1] else None} for r in rows]}
    except Exception as e:  # noqa: BLE001 - marks degraded, never a silent fake zero
        logger.warning("jobs metrics degraded: %s", e)
        result["degraded"].append("jobs")
        result.setdefault("jobs", {})["last_24h"] = {"success": 0, "failed": 0, "running": 0}
        result["jobs"]["degraded"] = True
        result["schedules"] = {"upcoming": []}

    # ── Rich dashboard extras (Feature 12 / dashboard recreate) ─────────────
    from datetime import timedelta as _td
    result.setdefault("jobs", {})
    day_keys = [(now.date() - _td(days=i)).isoformat() for i in range(6, -1, -1)]
    activity = {k: {"date": k, "jobs": 0, "sessions": 0} for k in day_keys}
    week_cutoff = now - timedelta(days=7)
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        async with _get_session(settings.automation_db_name)() as s:
            jobs_today = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_job_runs WHERE started_at >= :c"), {"c": today0})).scalar() or 0
            jobs_week = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_job_runs WHERE started_at >= :c"), {"c": week_cutoff})).scalar() or 0
            ok_week = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_job_runs WHERE status='success' AND started_at >= :c"), {"c": week_cutoff})).scalar() or 0
            failed_week = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_job_runs WHERE status IN ('failed','error') AND started_at >= :c"), {"c": week_cutoff})).scalar() or 0
            manual = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_job_runs WHERE triggered_by LIKE 'user:%' AND started_at >= :c"), {"c": week_cutoff})).scalar() or 0
            auto = (await s.execute(text("SELECT COUNT(*) FROM v_metrics_job_runs WHERE triggered_by NOT LIKE 'user:%' AND started_at >= :c"), {"c": week_cutoff})).scalar() or 0
            done = ok_week + failed_week
            result["jobs"].update({
                "today": jobs_today, "week": jobs_week, "ok_week": ok_week, "failed_week": failed_week,
                "success_rate": round(100 * ok_week / done) if done else 100,
                "manual": manual, "auto": auto,
            })
            rows = (await s.execute(text(
                "SELECT r.id, r.status, r.triggered_by, r.started_at, COALESCE(t.name, r.job_template_id) "
                "FROM v_metrics_job_runs r LEFT JOIN v_metrics_job_templates t ON t.id=r.job_template_id "
                "ORDER BY r.started_at DESC LIMIT 6"))).fetchall()
            result["recent_jobs"] = [{"id": r[0], "status": r[1], "triggered_by": r[2],
                                      "ts": r[3].isoformat() if r[3] else None, "playbook": r[4]} for r in rows]
            rows = (await s.execute(text(
                "SELECT COALESCE(t.name, r.job_template_id) n, COUNT(*) c FROM v_metrics_job_runs r "
                "LEFT JOIN v_metrics_job_templates t ON t.id=r.job_template_id GROUP BY n ORDER BY c DESC LIMIT 5"))).fetchall()
            result["top_playbooks"] = [{"name": r[0], "runs": r[1]} for r in rows]
            rows = (await s.execute(text(
                "SELECT COALESCE(t.name, '(deleted)'), r.started_at FROM v_metrics_job_runs r "
                "LEFT JOIN v_metrics_job_templates t ON t.id=r.job_template_id WHERE r.status IN ('failed','error') "
                "ORDER BY r.started_at DESC LIMIT 5"))).fetchall()
            result["recent_failures"] = [{"playbook": r[0], "ts": r[1].isoformat() if r[1] else None} for r in rows]
            rows = (await s.execute(text(
                "SELECT to_char(date_trunc('day', started_at),'YYYY-MM-DD') d, COUNT(*) FROM v_metrics_job_runs "
                "WHERE started_at >= :c GROUP BY d"), {"c": week_cutoff})).fetchall()
            for d, c in rows:
                if d in activity:
                    activity[d]["jobs"] = c
    except Exception as e:  # noqa: BLE001 - marks degraded, never a silent fake zero
        logger.warning("jobs detail degraded: %s", e)
        if "jobs" not in result["degraded"]:
            result["degraded"].append("jobs")
        result.setdefault("recent_jobs", []); result.setdefault("top_playbooks", []); result.setdefault("recent_failures", [])

    try:
        async with _get_session(settings.terminal_db_name)() as s:
            rows = (await s.execute(text(
                "SELECT username, host_name, started_at FROM v_metrics_ssh_sessions WHERE status='active' ORDER BY started_at DESC LIMIT 10"))).fetchall()
            result["active_sessions"] = [{"user": r[0], "host": r[1], "since": r[2].isoformat() if r[2] else None} for r in rows]
            rows = (await s.execute(text(
                "SELECT to_char(date_trunc('day', started_at),'YYYY-MM-DD') d, COUNT(*) FROM v_metrics_ssh_sessions "
                "WHERE started_at >= :c GROUP BY d"), {"c": week_cutoff})).fetchall()
            for d, c in rows:
                if d in activity:
                    activity[d]["sessions"] = c
    except Exception as e:  # noqa: BLE001 - marks degraded, never a silent fake zero
        logger.warning("active sessions degraded: %s", e)
        if "sessions" not in result["degraded"]:
            result["degraded"].append("sessions")
        result.setdefault("active_sessions", [])

    result["activity_7d"] = [activity[k] for k in day_keys]
    _dash_cache["data"] = result
    _dash_cache["ts"] = time.monotonic()
    return result


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return _metrics.snapshot()
