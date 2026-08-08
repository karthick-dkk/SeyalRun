from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from libs.obsmetrics import ServiceMetrics
from libs.securelog import configure_logging
from libs.servicetoken import mint

from . import http as _http
from . import platform_settings
from . import rbac
from .collectors import background_metric_refresh
from .config import get_settings
from .proxy import filtered_hosts, kiosk_filtered_hosts, proxy
from .rate_limit import check_rate_limit
from .redis_client import redis_client
from .routers.health import router as health_router
from .routers.metrics import router as metrics_router
from .security import (
    SESSION_COOKIE_NAME,
    SESSION_PREFIX,
    USER_SESSION_PREFIX,
    AuthError,
    resolve_identity,
    resolve_identity_from_bearer_or_cookie,
)
from .ws_proxy import router as ws_router

settings = get_settings()
configure_logging(settings.service_name, settings.log_level, settings.log_path)

_refresh_task: asyncio.Task | None = None
_roles_task: asyncio.Task | None = None
_settings_task: asyncio.Task | None = None
import logging as _logging
_log = _logging.getLogger(__name__)


async def _platform_settings_refresh_loop() -> None:
    while True:
        await platform_settings.refresh()
        await asyncio.sleep(40)


async def _load_role_perms() -> None:
    """Fetch role permission documents from identity-service into the RBAC cache."""
    try:
        token = mint("api-gateway", "identity-service", settings.service_jwt_secret)
        resp = await _http.client.get(f"{settings.identity_service_url}/api/v1/roles",
                                      headers={"X-Service-Token": token}, timeout=5.0)
        if resp.status_code == 200:
            rbac.set_role_perms({r["name"]: (r.get("permissions") or {}) for r in resp.json()})
    except Exception as exc:  # noqa: BLE001
        _log.warning("role perms refresh failed: %s", exc)


async def _roles_refresh_loop() -> None:
    while True:
        await _load_role_perms()
        await asyncio.sleep(45)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _refresh_task, _roles_task, _settings_task
    _refresh_task = asyncio.create_task(
        background_metric_refresh(settings.redis_url, settings.metrics_refresh_interval_seconds)
    )
    await _load_role_perms()   # warm the RBAC cache before serving traffic
    _roles_task = asyncio.create_task(_roles_refresh_loop())
    await platform_settings.refresh()   # warm the platform-settings cache too
    _settings_task = asyncio.create_task(_platform_settings_refresh_loop())
    try:
        yield
    finally:
        for task in (_refresh_task, _roles_task, _settings_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await _http.aclose()


app = FastAPI(title="SeyalRun API Gateway", version="2.0.0", lifespan=lifespan)
app.include_router(ws_router)
app.include_router(health_router)
app.include_router(metrics_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Endpoints reachable without a session JWT or PAT. auth/zbx-sso-init is the Zabbix
# module's own entry point (HMAC-authenticated inside identity-service, not by a
# SeyalRun session) — it must be reachable before any SeyalRun login exists.
_PUBLIC_PATHS = {
    "auth/login", "auth/sso-exchange", "auth/zbx-sso-init",
    # Email-based authorization approval — the click itself carries no session;
    # the single-use token in the query string is the only credential (see
    # identity-service/app/api/authorizations.py::authorization_email_action).
    "authorizations/email-action",
}

_metrics = ServiceMetrics()
app.middleware("http")(_metrics.middleware)


@app.get("/health")
async def health():
    checks: dict[str, bool] = {}
    async with httpx.AsyncClient(timeout=3.0) as client:
        for name, url in (
            ("identity_service", f"{settings.identity_service_url}/health"),
            ("inventory_service", f"{settings.inventory_service_url}/health"),
            ("terminal_service", f"{settings.terminal_service_url}/health"),
            ("recording_service", f"{settings.recording_service_url}/health"),
        ):
            try:
                resp = await client.get(url)
                checks[name] = resp.status_code == 200
            except httpx.HTTPError:
                checks[name] = False

    try:
        await redis_client.ping()
        checks["redis"] = True
    except redis.RedisError:
        checks["redis"] = False

    return {"status": "ok" if all(checks.values()) else "degraded", "service": settings.service_name, **checks}


@app.get("/api/v1/health")
async def health_v1():
    """Same as /health, exposed under /api/v1/ so edge-proxy's /api/ location
    gives external callers (and the staging verify script) a single
    aggregate health check without authentication."""
    return await health()


@app.get("/metrics")
async def metrics():
    from . import obs

    # upstreams: per-service latency/error snapshot — extra JSON the Zabbix
    # monitor ignores but /api/metrics/response-times consumers already use.
    return _metrics.snapshot(upstreams=obs.snapshot())


@app.get("/api/v1/auth/nav")
async def auth_nav(request: Request):
    """The caller's accessible nav areas, so the frontend hides pages they can't open.
    Registered before the catch-all proxy so it isn't forwarded to identity-service."""
    try:
        identity = await resolve_identity(request.headers.get("authorization"))
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    roles = identity.get("roles") or [identity.get("role", "user")]
    return rbac.nav_permissions(roles)


@app.get("/api/v1/auth/session")
async def auth_session(request: Request):
    """Minimal identity for bootstrapping a tab that has no in-memory token
    yet — e.g. the `_session` URL-fragment handoff, or (new) a brand-new tab
    opened from an external link (a Zabbix "Terminal" action) that shares no
    JS memory with an already-logged-in console tab. In that second case
    there is no Authorization header at all; fall back to the httpOnly
    sr_session bootstrap cookie (see /auth/login) and, if that's what
    resolved it, hand the opaque session id back so the tab can adopt it as
    its own ordinary in-memory Bearer token from then on."""
    authorization = request.headers.get("authorization")
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    try:
        identity = await resolve_identity_from_bearer_or_cookie(authorization, cookie_token)
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    out = {
        "id": identity["user_id"],
        "username": identity.get("username", ""),
        "role_name": identity.get("role", "user"),
        "roles": identity.get("roles") or [identity.get("role", "user")],
        "must_change_password": bool(identity.get("pwc")),
        # Surfaced for symmetry with must_change_password — a tab that reloads
        # mid-gate (pending MFA verify, or group-forced enrollment) can at least
        # see why every other call 403s, even though no route currently redirects
        # on these (same pre-existing limitation must_change_password already had).
        "mfa_pending": bool(identity.get("mfa_pending")),
        "mfa_setup_required": bool(identity.get("mfa_setup_required")),
        "kiosk": bool(identity.get("kiosk_host_id")),
        "kiosk_host_id": identity.get("kiosk_host_id"),
    }
    if not authorization and cookie_token:
        out["access_token"] = cookie_token
    return out


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def auth_logout(request: Request, response: Response):
    """Instant server-side revocation of the caller's own session — `DEL
    session:<id>` in Redis. The token is meaningless the moment this returns;
    handled entirely in the gateway since it's the one holding the raw opaque
    token (identity-service never sees it after minting). Also clears the
    sr_session bootstrap cookie if one was set — it references the same
    Redis key, so it's already dead, this just tidies up the browser.

    Also clears the single-session-per-user pointer (identity-service/app/
    sessions.py::USER_SESSION_PREFIX) — but ONLY if it still points at the
    session being logged out. If the user already logged in again elsewhere
    before this call landed, that newer session owns the pointer now and this
    logout must not touch it (deleting it would just mean the next login has
    no prior session to kick out — harmless, but still the wrong owner's data)."""
    authorization = request.headers.get("authorization") or ""
    if authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        if token and not token.startswith("sr_"):
            raw = await redis_client.get(f"{SESSION_PREFIX}{token}")
            if raw:
                try:
                    user_id = json.loads(raw).get("user_id")
                except (TypeError, ValueError):
                    user_id = None
                if user_id:
                    current_pointer = await redis_client.get(f"{USER_SESSION_PREFIX}{user_id}")
                    if current_pointer == token:
                        await redis_client.delete(f"{USER_SESSION_PREFIX}{user_id}")
            await redis_client.delete(f"{SESSION_PREFIX}{token}")
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@app.post("/api/v1/auth/touch", status_code=status.HTTP_204_NO_CONTENT)
async def auth_touch(request: Request):
    """Lightweight heartbeat: authenticating at all already slides the
    session's idle window forward (see security.py::lookup_session) — this
    endpoint exists purely so the frontend has something cheap to call while
    a user is working entirely inside an open terminal WebSocket (which
    never touches the main REST session once connected)."""
    try:
        await resolve_identity(request.headers.get("authorization"))
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


async def _integration_settings() -> tuple[str, str, str]:
    """(console_url, api_url, api_token) — DB setting (superadmin-editable) wins over
    .env for console/api url; the token is DB-only (never had a .env fallback here —
    it's never exposed to the browser, only used server-side for the token-validity
    probe in integration_info() below)."""
    db_console, db_api, db_token = "", "", ""
    try:
        tok = mint("api-gateway", "identity-service", settings.service_jwt_secret)
        r = await _http.client.get(
            f"{settings.identity_service_url}/api/v1/internal/settings/integration",
            headers={"X-Service-Token": tok}, timeout=4.0,
        )
        if r.status_code == 200:
            d = r.json()
            db_console = (d.get("zabbix_console_url") or "").strip()
            db_api = (d.get("zabbix_api_url") or "").strip()
            db_token = (d.get("zabbix_api_token") or "").strip()
    except httpx.HTTPError:
        pass
    return (db_console or (settings.zabbix_console_url or "").strip(),
            db_api or (settings.zabbix_api_url or "").strip(),
            db_token)


@app.get("/api/v1/integration/info")
async def integration_info(request: Request):
    """Zabbix integration URL + reachability for the header link and Integration page.
    URL/console come from the DB setting (superadmin-editable) or .env; the API token is
    never exposed to the browser."""
    try:
        await resolve_identity(request.headers.get("authorization"))
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    console, api_url, api_token = await _integration_settings()
    # Only trust a console URL that actually looks like one (guards against a leaked
    # .env.example comment becoming the value); otherwise fall back to the API URL.
    if not console.startswith("http"):
        console = ""
    url = (console or api_url or "").rstrip("/")
    reachable, version = False, ""
    # token_valid stays None (not tested) when there's no token to test, or the plain
    # API reachability check above already failed — no point probing auth on a
    # server that isn't even answering. Only True/False once genuinely tested.
    token_valid: bool | None = None
    if api_url:
        api = api_url.rstrip("/") + "/api_jsonrpc.php"
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.post(api, json={"jsonrpc": "2.0", "method": "apiinfo.version", "params": {}, "id": 1})
                version = str((r.json() or {}).get("result", ""))
                reachable = bool(version)
                if reachable and api_token:
                    # Cheap authenticated call — proves the token actually works, which
                    # apiinfo.version (unauthenticated) can never tell us. A bad/expired
                    # token comes back as a JSON-RPC error, not an HTTP error.
                    r2 = await client.post(
                        api, json={"jsonrpc": "2.0", "method": "host.get", "params": {"countOutput": True}, "id": 1},
                        headers={"Authorization": f"Bearer {api_token}"},
                    )
                    token_valid = "error" not in (r2.json() or {})
        except Exception:  # noqa: BLE001
            reachable = False
    return {
        "zabbix_url": url, "api_url": api_url or "", "configured": bool(api_url),
        "reachable": reachable, "version": version,
        "token_configured": bool(api_token), "token_valid": token_valid,
    }


@app.get("/api/v1/admin/attest")
async def admin_attest(request: Request):
    """Live security self-attestation (superadmin only). Each check is PROVEN at
    runtime — we actually exercise the invariant rather than asserting it from
    config — so the dashboard can render a green/red badge backed by reality."""
    try:
        identity = await resolve_identity(request.headers.get("authorization"))
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if "superadmin" not in (identity.get("roles") or [identity.get("role", "user")]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="superadmin required")

    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    add("tls_in_use", request.headers.get("x-forwarded-proto") == "https",
        "edge-proxy terminates TLS")
    add("gateway_secrets_set", bool(settings.service_jwt_secret),
        "SERVICE_JWT_SECRET configured")

    # metrics-service must reject an unauthenticated dashboard request (guards H1).
    try:
        r = await _http.client.get(f"{settings.metrics_service_url}/api/v1/metrics/dashboard", timeout=4.0)
        add("metrics_token_enforced", r.status_code == 401,
            f"dashboard without service token -> {r.status_code} (want 401)")
    except httpx.HTTPError as exc:
        add("metrics_token_enforced", False, f"probe failed: {exc}")

    # webhook must reject an unsigned request (guards H3 — HMAC mandatory).
    try:
        r = await _http.client.post(
            f"{settings.zabbix_integration_service_url}/api/v1/webhook/zabbix",
            content=b"{}", timeout=4.0,
        )
        add("webhook_hmac_enforced", r.status_code in (401, 403),
            f"unsigned webhook -> {r.status_code} (want 401)")
    except httpx.HTTPError as exc:
        add("webhook_hmac_enforced", False, f"probe failed: {exc}")

    return {"attested": all(c["pass"] for c in checks), "checks": checks, "ts": time.time()}


@app.get("/api/v1/admin/health")
async def admin_health(request: Request):
    """Live health of every SeyalRun service + per-upstream response times (gateway obs)."""
    try:
        await resolve_identity(request.headers.get("authorization"))
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    targets = {
        "api-gateway": None,
        "identity-service": f"{settings.identity_service_url}/health",
        "inventory-service": f"{settings.inventory_service_url}/health",
        "terminal-service": f"{settings.terminal_service_url}/health",
        "recording-service": f"{settings.recording_service_url}/health",
        "automation-service": f"{settings.automation_service_url}/health",
        "zabbix-integration-service": f"{settings.zabbix_integration_service_url}/health",
        "metrics-service": f"{settings.metrics_service_url}/health",
    }

    async def probe(name: str, url: str | None) -> dict:
        if url is None:
            return {"service": name, "status": "ok", "http": 200, "latency_ms": 0, "detail": "gateway"}
        t = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                r = await client.get(url)
            ms = round((time.perf_counter() - t) * 1000)
            try:
                j = r.json()
            except Exception:  # noqa: BLE001
                j = {}
            ok = r.status_code == 200
            detail = ("db ok" if j.get("db") else "db down") if "db" in j else ""
            return {"service": name, "status": (j.get("status") if ok else "down") or ("ok" if ok else "down"),
                    "http": r.status_code, "latency_ms": ms, "detail": detail}
        except Exception as exc:  # noqa: BLE001
            return {"service": name, "status": "down", "http": 0,
                    "latency_ms": round((time.perf_counter() - t) * 1000), "detail": type(exc).__name__}

    services = list(await asyncio.gather(*[probe(n, u) for n, u in targets.items()]))
    try:
        await redis_client.ping()
        redis_ok = True
    except Exception:  # noqa: BLE001
        redis_ok = False
    services.append({"service": "redis", "status": "ok" if redis_ok else "down", "http": 0, "latency_ms": 0, "detail": ""})

    from . import obs
    return {"services": services, "latency": obs.snapshot()}


@app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def gateway(path: str, request: Request):
    if path == "internal" or path.startswith("internal/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    identity: dict | None = None
    if path not in _PUBLIC_PATHS:
        try:
            identity = await resolve_identity(request.headers.get("authorization"))
        except AuthError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

        # Forced password rotation: a session minted from default seed credentials
        # may ONLY change its password (plus the nav probe the login flow calls).
        if identity.get("pwc") and path not in ("auth/change-password", "auth/nav"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="password change required: POST /api/v1/auth/change-password",
            )

        # Login-time MFA gate: a session minted with MFA pending may ONLY verify
        # its code, resend an email OTP, or probe nav — mirrors the pwc block above.
        if identity.get("mfa_pending") and path not in ("auth/mfa/verify-login", "auth/mfa/resend", "auth/nav"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="MFA verification required: POST /api/v1/auth/mfa/verify-login",
            )

        # Group-enforced MFA gate: the caller has no mfa_method yet but belongs to a
        # group that requires one — locked to the enrollment endpoints (not
        # verify-login, since there's no code to verify yet; enrollment IS the proof).
        _mfa_enroll_paths = ("auth/mfa/setup", "auth/mfa/setup-email", "auth/mfa/enable", "auth/nav")
        if identity.get("mfa_setup_required") and path not in _mfa_enroll_paths:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="your group requires MFA: POST /api/v1/auth/mfa/setup",
            )

        # Zero-trust: deny anything the caller's roles don't explicitly grant.
        # Exception: group-enforced MFA overrides the role-level "mfa" capability
        # flag (rbac.is_authorized's special-case for these same 3 enrollment paths)
        # — a user forced into MFA by their group may always enroll, regardless of
        # whether their role separately carries the "mfa" flag.
        _mfa_enforced_bypass = identity.get("mfa_setup_required") and path in (
            "auth/mfa/setup", "auth/mfa/setup-email", "auth/mfa/enable",
        )
        if not _mfa_enforced_bypass and not rbac.is_authorized(
            identity.get("roles") or [identity.get("role", "user")], request.method, path
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden: your role does not permit this action")
        # Preserve the caller's true (pre-elevation) primary role — downstream_role()
        # below can "vouch" a support/custom write up to X-User-Role: admin (see its
        # own docstring), which is right for simple admin-gated CRUD guards but means
        # a resource-scoped check (e.g. "does this support user actually manage this
        # zone") can't tell a real admin from a vouched-for support write using
        # X-User-Role alone. X-User-Real-Role carries the un-elevated role for the
        # few downstream checks that need that distinction.
        identity["real_role"] = rbac.primary_role(identity.get("roles") or [identity.get("role", "user")])
        # Forward the effective downstream role (see rbac.downstream_role for the rules).
        identity["role"] = rbac.downstream_role(
            identity.get("roles") or [identity.get("role", "user")], request.method, path.split("/", 1)[0])

    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{identity['user_id'] if identity else 'anon'}"
    # Elevated bucket for trusted, module-originated traffic — auth/zbx-sso-init is the
    # only endpoint the Zabbix module itself calls (every other embedded-page call comes
    # from the end user's own browser session and stays on the ordinary per-user limit);
    # a configured trusted IP is an additional, optional signal for future module traffic.
    is_module_traffic = platform_settings.zabbix_module_enabled() and (
        path == "auth/zbx-sso-init" or client_ip in platform_settings.zabbix_module_trusted_ips()
    )
    rate_limit = platform_settings.zabbix_module_elevated_rate_limit() if is_module_traffic \
        else platform_settings.rate_limit_requests()
    allowed = await check_rate_limit(redis_client, rate_key, rate_limit, platform_settings.rate_limit_window_seconds())
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded")

    if path == "hosts" and request.method == "GET" and identity and identity.get("kiosk_host_id"):
        return await kiosk_filtered_hosts(identity["kiosk_host_id"])
    if path == "hosts" and request.method == "GET" and identity and not rbac.sees_all_hosts(identity.get("roles") or []):
        return await filtered_hosts(identity)

    response = await proxy(request, path, identity or {})

    # Cross-tab bootstrap cookie — set ONLY on a direct login/password-change
    # (never /auth/sso-exchange, the Zabbix-iframe flow; see security.py's
    # SESSION_COOKIE_NAME docstring for why). httpOnly, so JS never reads it;
    # it exists purely so a brand-new tab with no in-memory token (e.g. a
    # Zabbix "Terminal" link opened as an ordinary top-level tab) can silently
    # recognize an already-logged-in browser via GET /auth/session instead of
    # always forcing a fresh login.
    if path in ("auth/login", "auth/change-password") and 200 <= response.status_code < 300:
        try:
            token = json.loads(response.body).get("access_token")
        except (ValueError, AttributeError):
            token = None
        if token:
            response.set_cookie(
                key=SESSION_COOKIE_NAME, value=token,
                httponly=True, secure=True, samesite="lax",
                max_age=platform_settings.session_absolute_hours() * 3600,
                path="/",
            )

    # Refresh the RBAC cache immediately when a role mutation flows through, so a
    # created/edited/deleted role takes effect at once instead of waiting up to 45s for
    # the background refresh loop. Best-effort and non-blocking.
    if (path.split("/", 1)[0] == "roles"
            and request.method in ("POST", "PUT", "PATCH", "DELETE")
            and 200 <= response.status_code < 300):
        asyncio.create_task(_load_role_perms())

    return response
