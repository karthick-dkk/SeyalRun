"""Playbook Studio — FastAPI application entrypoint."""

from __future__ import annotations

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .api import alerts, jobs, modules, playbooks, ssh, templates
from .config import get_settings
from .database import engine
from .dependencies import get_current_user
from .ws import ssh_terminal, stream

log = structlog.get_logger(__name__)
settings = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Playbook Studio",
        description="Visual Ansible Playbook Builder for SeyalRun",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS — allow seyalrun-console origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID + structured logging middleware
    app.add_middleware(_RequestLogMiddleware)

    # Routers
    app.include_router(modules.router)
    app.include_router(playbooks.router)
    app.include_router(templates.router)
    app.include_router(jobs.router)
    app.include_router(alerts.router)
    app.include_router(ssh.router)
    app.include_router(stream.router)
    app.include_router(ssh_terminal.router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok", "service": "playbook-studio"}

    @app.get("/api/v1/health", tags=["ops"])
    async def health_v1() -> dict:
        return {"status": "ok", "service": "playbook-studio"}

    @app.get("/api/v1/assets", tags=["assets"])
    async def list_assets(user: dict = Depends(get_current_user)) -> dict:
        """Proxy JumpServer assets for host selection in playbook runs."""
        import httpx

        try:
            async with httpx.AsyncClient(verify=False, timeout=10) as client:
                r = await client.get(
                    f"{settings.jumpserver_api_url}/api/v1/assets/assets/?limit=200&offset=0",
                    headers={"Authorization": f"Bearer {user['token']}"},
                )
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            log.warning("assets_proxy_error", error=str(exc))
            return {"total": 0, "items": []}

        results = data.get("results", data if isinstance(data, list) else [])

        def _domain_name(a: dict) -> str:
            d = a.get("domain")
            if isinstance(d, dict):
                return d.get("name", "")
            return ""

        items = [
            {
                "id": str(a.get("id", "")),
                "name": a.get("name", ""),
                "address": a.get("address", a.get("ip", "")),
                "platform": a.get("platform", {}).get("name", "")
                if isinstance(a.get("platform"), dict)
                else str(a.get("platform", "")),
                "is_active": a.get("is_active", True),
                "comment": a.get("comment", ""),
                "domain_id": str(a.get("domain", {}).get("id", ""))
                if isinstance(a.get("domain"), dict)
                else "",
                "domain_name": _domain_name(a),
            }
            for a in results
        ]
        return {"total": len(items), "items": items}

    @app.get("/api/v1/assets/{asset_id}/accounts", tags=["assets"])
    async def get_asset_accounts(asset_id: str, user: dict = Depends(get_current_user)) -> dict:
        """Fetch stored SSH accounts for an asset from JumpServer (usernames only, no passwords)."""
        import httpx

        accounts: list[dict] = []

        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            for url in [
                f"{settings.jumpserver_api_url}/api/v1/accounts/accounts/?asset={asset_id}&limit=50",
                f"{settings.jumpserver_api_url}/api/v1/perms/users/{user['id']}/assets/{asset_id}/accounts/",
            ]:
                try:
                    r = await client.get(url, headers={"Authorization": f"Bearer {user['token']}"})
                    if r.status_code == 200:
                        data = r.json()
                        results = data.get("results", data if isinstance(data, list) else [])
                        accounts = []
                        for a in results:
                            if not a.get("username"):
                                continue
                            raw_st = a.get("secret_type", "password")
                            secret_type = (
                                raw_st.get("value", str(raw_st))
                                if isinstance(raw_st, dict)
                                else str(raw_st)
                            )
                            accounts.append(
                                {
                                    "id": str(a.get("id", "")),
                                    "name": a.get("name", a.get("username", "")),
                                    "username": a.get("username", ""),
                                    "secret_type": secret_type,
                                    "privileged": a.get("privileged", False),
                                }
                            )
                        if accounts:
                            break
                except Exception:
                    continue

        return {"asset_id": asset_id, "accounts": accounts}

    @app.get("/api/v1/assets/{asset_id}/connectivity", tags=["assets"])
    async def get_asset_connectivity(asset_id: str, user: dict = Depends(get_current_user)) -> dict:
        """Fetch domain + gateway info for an asset (for ProxyJump connectivity)."""
        import httpx

        token = user["token"]
        headers = {"Authorization": f"Bearer {token}"}

        domain_id = domain_name = gateway_host = gateway_port = None
        gateway_accounts: list[dict] = []

        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            # Step 1: get asset detail → extract domain
            try:
                r = await client.get(
                    f"{settings.jumpserver_api_url}/api/v1/assets/assets/{asset_id}/",
                    headers=headers,
                )
                if r.status_code == 200:
                    asset_data = r.json()
                    domain = asset_data.get("domain")
                    if isinstance(domain, dict) and domain.get("id"):
                        domain_id = str(domain["id"])
                        domain_name = domain.get("name", "")
            except Exception:
                pass

            # Step 2: if domain found, get its gateways
            if domain_id:
                for gw_url in [
                    f"{settings.jumpserver_api_url}/api/v1/assets/domains/{domain_id}/gateways/",
                    f"{settings.jumpserver_api_url}/api/v1/assets/gateways/?domain={domain_id}&limit=10",
                ]:
                    try:
                        r = await client.get(gw_url, headers=headers)
                        if r.status_code == 200:
                            gw_data = r.json()
                            gateways = gw_data.get(
                                "results", gw_data if isinstance(gw_data, list) else []
                            )
                            if gateways:
                                gw = gateways[0]
                                gateway_host = gw.get("address", gw.get("ip", ""))
                                gateway_port = str(gw.get("port", 22))
                                gw_id = str(gw.get("id", ""))

                                # Step 3: get gateway's stored accounts
                                if gw_id:
                                    ar = await client.get(
                                        f"{settings.jumpserver_api_url}/api/v1/accounts/accounts/?asset={gw_id}&limit=20",
                                        headers=headers,
                                    )
                                    if ar.status_code == 200:
                                        acc_data = ar.json()
                                        acc_list = acc_data.get(
                                            "results",
                                            acc_data if isinstance(acc_data, list) else [],
                                        )
                                        for a in acc_list:
                                            if a.get("username"):
                                                raw_st = a.get("secret_type", "password")
                                                secret_type = (
                                                    raw_st.get("value", str(raw_st))
                                                    if isinstance(raw_st, dict)
                                                    else str(raw_st)
                                                )
                                                gateway_accounts.append(
                                                    {
                                                        "username": a.get("username", ""),
                                                        "secret_type": secret_type,
                                                        "privileged": a.get("privileged", False),
                                                    }
                                                )
                                break
                    except Exception:
                        continue

        has_gateway = bool(gateway_host)
        return {
            "asset_id": asset_id,
            "has_gateway": has_gateway,
            "domain": {"id": domain_id, "name": domain_name} if domain_id else None,
            "gateway": {
                "host": gateway_host,
                "port": gateway_port or "22",
                "accounts": gateway_accounts,
            }
            if has_gateway
            else None,
        }

    @app.get("/api/v1/me", tags=["auth"])
    async def me(user: dict = Depends(get_current_user)) -> dict:
        """Validate JumpServer Bearer token and return user info (avoids browser CORS to JMS)."""
        return user

    @app.post("/api/v1/auth/login", tags=["auth"])
    async def login(body: dict) -> dict:
        """
        Authenticate with JumpServer username+password server-side.
        Returns {token, username, name, email} — browser never contacts JMS directly.
        """
        import httpx

        username = body.get("username", "")
        password = body.get("password", "")
        if not username or not password:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="username and password required"
            )
        try:
            async with httpx.AsyncClient(verify=False, timeout=10) as client:
                r = await client.post(
                    f"{settings.jumpserver_api_url}/api/v1/authentication/auth/",
                    json={"username": username, "password": password},
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code == 401 or r.status_code == 400:
                    from fastapi import HTTPException, status

                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid username or password",
                    )
                r.raise_for_status()
                data = r.json()
        except httpx.RequestError as exc:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail="Cannot reach JumpServer"
            ) from exc

        token = data.get("token", "")
        user_data = data.get("user", {})
        return {
            "token": token,
            "id": str(user_data.get("id", "")),
            "username": user_data.get("username", username),
            "name": user_data.get("name", username),
            "email": user_data.get("email", ""),
        }

    @app.get("/api/v1/dashboard", tags=["dashboard"])
    async def dashboard(user: dict = Depends(get_current_user)) -> dict:
        """Aggregate stats for the dashboard: assets, sessions, jobs, activity chart."""
        import datetime

        import httpx
        from sqlalchemy import func, select

        from .database import AsyncSessionLocal
        from .models.domain import Job, Playbook, SSHSession

        now = datetime.datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since_24h = now - datetime.timedelta(hours=24)

        async with AsyncSessionLocal() as db:
            # ── Job stats ──────────────────────────────────────────────
            total_jobs = (await db.execute(select(func.count()).select_from(Job))).scalar() or 0

            jobs_today = (
                await db.execute(
                    select(func.count()).select_from(Job).where(Job.created_at >= today_start)
                )
            ).scalar() or 0

            jobs_success = (
                await db.execute(
                    select(func.count()).select_from(Job).where(Job.status == "success")
                )
            ).scalar() or 0

            jobs_failed = (
                await db.execute(
                    select(func.count()).select_from(Job).where(Job.status == "failed")
                )
            ).scalar() or 0

            # Recent jobs
            recent_jobs_rows = (
                (await db.execute(select(Job).order_by(Job.created_at.desc()).limit(8)))
                .scalars()
                .all()
            )
            recent_jobs = [
                {
                    "id": str(j.id),
                    "status": j.status,
                    "triggered_by": j.triggered_by,
                    "duration_seconds": j.duration_seconds,
                    "exit_code": j.exit_code,
                    "created_at": j.created_at.isoformat() if j.created_at else None,
                }
                for j in recent_jobs_rows
            ]

            # Playbook count
            total_playbooks = (
                await db.execute(
                    select(func.count()).select_from(Playbook).where(Playbook.is_template == False)  # noqa: E712
                )
            ).scalar() or 0

            # ── SSH Session stats ──────────────────────────────────────
            total_sessions = (
                await db.execute(select(func.count()).select_from(SSHSession))
            ).scalar() or 0

            active_sessions = (
                await db.execute(
                    select(func.count())
                    .select_from(SSHSession)
                    .where(SSHSession.status == "active")
                )
            ).scalar() or 0

            failed_logins_24h = (
                await db.execute(
                    select(func.count())
                    .select_from(SSHSession)
                    .where(
                        SSHSession.status == "error",
                        SSHSession.started_at >= since_24h,
                    )
                )
            ).scalar() or 0

            # Recent SSH sessions
            recent_sessions_rows = (
                (
                    await db.execute(
                        select(SSHSession).order_by(SSHSession.started_at.desc()).limit(8)
                    )
                )
                .scalars()
                .all()
            )
            recent_sessions = [
                {
                    "id": str(s.id),
                    "user": s.user,
                    "asset_address": s.asset_address,
                    "asset_name": s.asset_name,
                    "ssh_username": s.ssh_username,
                    "status": s.status,
                    "duration_seconds": s.duration_seconds,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                }
                for s in recent_sessions_rows
            ]

            # Failed logins (all time, last 10)
            failed_sessions_rows = (
                (
                    await db.execute(
                        select(SSHSession)
                        .where(SSHSession.status == "error")
                        .order_by(SSHSession.started_at.desc())
                        .limit(10)
                    )
                )
                .scalars()
                .all()
            )
            failed_sessions = [
                {
                    "id": str(s.id),
                    "user": s.user,
                    "asset_address": s.asset_address,
                    "ssh_username": s.ssh_username,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                }
                for s in failed_sessions_rows
            ]

            # ── Activity chart — last 7 days ───────────────────────────
            labels = []
            sessions_per_day = []
            jobs_per_day = []

            for i in range(6, -1, -1):
                day = (now - datetime.timedelta(days=i)).date()
                day_start = datetime.datetime.combine(day, datetime.time.min)
                day_end = datetime.datetime.combine(day, datetime.time.max)

                labels.append(day.strftime("%b %d"))

                s_count = (
                    await db.execute(
                        select(func.count())
                        .select_from(SSHSession)
                        .where(
                            SSHSession.started_at >= day_start,
                            SSHSession.started_at <= day_end,
                        )
                    )
                ).scalar() or 0
                sessions_per_day.append(s_count)

                j_count = (
                    await db.execute(
                        select(func.count())
                        .select_from(Job)
                        .where(
                            Job.created_at >= day_start,
                            Job.created_at <= day_end,
                        )
                    )
                ).scalar() or 0
                jobs_per_day.append(j_count)

        # ── Asset count from JumpServer ────────────────────────────────
        total_assets = 0
        try:
            async with httpx.AsyncClient(verify=False, timeout=8) as client:
                r = await client.get(
                    f"{settings.jumpserver_api_url}/api/v1/assets/assets/?limit=1",
                    headers={"Authorization": f"Bearer {user['token']}"},
                )
                if r.status_code == 200:
                    data = r.json()
                    total_assets = data.get("count", len(data.get("results", [])))
        except Exception:
            pass

        success_rate = round(jobs_success / max(1, jobs_success + jobs_failed) * 100, 1)

        return {
            "stats": {
                "total_assets": total_assets,
                "total_playbooks": total_playbooks,
                "total_sessions": total_sessions,
                "active_sessions": active_sessions,
                "failed_logins_24h": failed_logins_24h,
                "total_jobs": total_jobs,
                "jobs_today": jobs_today,
                "jobs_success": jobs_success,
                "jobs_failed": jobs_failed,
                "success_rate": success_rate,
            },
            "recent_sessions": recent_sessions,
            "failed_sessions": failed_sessions,
            "recent_jobs": recent_jobs,
            "activity_chart": {
                "labels": labels,
                "sessions": sessions_per_day,
                "jobs": jobs_per_day,
            },
        }

    # ── SSH Settings ──────────────────────────────────────────────────────────

    @app.get("/api/v1/settings", tags=["settings"])
    async def get_app_settings(user: dict = Depends(get_current_user)) -> dict:
        from .services.ssh_pool import get_settings_all

        return get_settings_all()

    @app.patch("/api/v1/settings", tags=["settings"])
    async def update_app_settings(body: dict, user: dict = Depends(get_current_user)) -> dict:
        from .services.ssh_pool import update_settings

        allowed = {"ssh_idle_timeout_minutes"}
        patch = {k: v for k, v in body.items() if k in allowed}
        if "ssh_idle_timeout_minutes" in patch:
            val = float(patch["ssh_idle_timeout_minutes"])
            patch["ssh_idle_timeout_minutes"] = max(1, min(val, 480))  # 1 min – 8 hours
        return update_settings(patch)

    @app.get("/api/v1/ssh/idle", tags=["ssh"])
    async def list_idle_sessions(user: dict = Depends(get_current_user)) -> dict:
        """List sessions currently kept alive in the idle pool."""
        from .services.ssh_pool import list_idle

        return {"items": list_idle()}

    @app.post("/api/v1/ssh/idle/{session_id}/resume", tags=["ssh"])
    async def resume_idle_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
        """Check if a session is in the idle pool (client will reconnect via WebSocket)."""
        from .services.ssh_pool import get_idle

        idle = get_idle(session_id)
        if not idle:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=404, detail="Session not in idle pool or already expired"
            )
        return {
            "session_id": session_id,
            "idle_seconds": round(idle.time_idle()),
            "buffered_frames": len(idle.buffer),
            "resumable": True,
        }

    @app.get("/ready", tags=["ops"])
    async def ready() -> dict:
        """Check DB and Redis connectivity."""
        from sqlalchemy import text

        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            return {"status": "not_ready", "db": str(exc)}

        try:
            from .dependencies import get_redis_client

            redis = get_redis_client()
            await redis.ping()
        except Exception as exc:
            return {"status": "not_ready", "redis": str(exc)}

        return {"status": "ready"}

    @app.on_event("startup")
    async def _startup() -> None:
        log.info("playbook_studio_starting", env=settings.environment)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await engine.dispose()
        log.info("playbook_studio_stopped")

    return app


class _RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        import time
        import uuid

        trace_id = str(uuid.uuid4())[:8]
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            trace_id=trace_id,
        )
        response.headers["X-Trace-Id"] = trace_id
        return response


app = create_app()
