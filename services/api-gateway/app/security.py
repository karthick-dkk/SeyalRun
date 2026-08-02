"""Resolves the caller's identity from either a server-side session (opaque
bearer token, looked up in Redis — see lookup_session) or a Personal Access
Token (PAT, prefix ``sr_``) sent as ``Authorization: Bearer <token>``.
"""

from __future__ import annotations

import json
import time

import httpx
import redis.asyncio as redis

from libs.servicetoken import mint

from . import http as _http
from . import platform_settings
from .config import get_settings
from .redis_client import redis_client

SESSION_PREFIX = "session:"
# Mirrors identity-service/app/sessions.py's USER_SESSION_PREFIX exactly (same
# Redis instance) — main.py's logout handler clears this pointer too.
USER_SESSION_PREFIX = "user_session:"
# Mirrors identity-service/app/sessions.py's ELEVATED_PREFIX exactly (same Redis
# instance) — PCI DSS Phase A admin/superadmin JIT elevation, set via
# POST /auth/elevate/verify.
ELEVATED_PREFIX = "elevated:"

# httpOnly cross-tab bootstrap cookie — set ONLY on a direct (non-iframe)
# /auth/login or /auth/change-password response, holding the SAME opaque
# session id as the in-memory Bearer token. JS can never read it (httpOnly);
# it exists solely so a brand-new tab (e.g. a Zabbix "Terminal" link opened
# as an ordinary top-level tab, which shares no JS memory with the console
# tab) can silently recognize an already-logged-in browser via GET
# /auth/session, instead of always forcing a fresh login. Deliberately never
# set on /auth/sso-exchange (the Zabbix-iframe flow) — a cookie set from
# within that third-party-iframe context would be blocked by Safari ITP
# today (and increasingly Chrome) anyway, so there is no benefit, only risk.
SESSION_COOKIE_NAME = "sr_session"


class AuthError(Exception):
    pass


# Live role cache: user_id → (roles, expiry). Roles are baked into the JWT at login, so a
# role/assignment change wouldn't apply until re-login. We re-read the user's CURRENT roles
# from identity-service (briefly cached) so grants take effect without forcing a re-login.
_ROLE_TTL = 30.0
_role_cache: dict[str, tuple[list[str], float]] = {}


async def _live_roles(user_id: str, fallback: list[str]) -> list[str]:
    now = time.time()
    hit = _role_cache.get(user_id)
    if hit and hit[1] > now:
        return hit[0]
    settings = get_settings()
    try:
        service_token = mint("api-gateway", "identity-service", settings.service_jwt_secret)
        resp = await _http.client.get(
            f"{settings.identity_service_url}/api/v1/internal/users/{user_id}/roles",
            headers={"X-Service-Token": service_token},
            timeout=3.0,
        )
        if resp.status_code == 200:
            body = resp.json()
            # known=True means identity spoke authoritatively — trust its roles even
            # when EMPTY (user's only role was a just-removed group → deny). Only fall
            # back to the stale JWT roles when identity couldn't answer (known=False /
            # legacy response without the flag AND non-empty). This is the OV2a fix:
            # an empty list must NOT silently inherit the JWT's old roles.
            if body.get("known") is True:
                roles = body.get("roles") or []
            else:
                roles = body.get("roles") or fallback
            _role_cache[user_id] = (roles, now + _ROLE_TTL)
            return roles
    except httpx.HTTPError:
        pass
    return fallback


async def _elevated_until(user_id: str) -> int:
    """0 if the user has no active elevated window (never set, expired-and-evicted
    by Redis TTL, or the store is briefly unreachable — fails closed, not open)."""
    try:
        raw = await redis_client.get(f"{ELEVATED_PREFIX}{user_id}")
    except redis.RedisError:
        return 0
    if not raw:
        return 0
    try:
        return int(json.loads(raw).get("until", 0))
    except (TypeError, ValueError):
        return 0


async def _with_live_roles(identity: dict) -> dict:
    """Replace the JWT's (possibly stale) roles with the user's current roles."""
    roles = await _live_roles(identity["user_id"], identity.get("roles") or [])
    identity["roles"] = roles
    if roles:
        identity["role"] = roles[0]
    identity["elevated_until"] = await _elevated_until(identity["user_id"])
    return identity


async def lookup_session(token: str) -> dict:
    """Look up an opaque session id in Redis and slide its expiry forward.

    The session is the sole source of truth — nothing is decoded from the
    token itself (it carries no claims, just an unguessable reference).
    Sliding: each successful lookup re-arms the idle window
    (session_idle_minutes from now) but never past the absolute ceiling
    (session_absolute_hours from the session's original creation time).
    Revocation is `DEL session:<id>` (see api-gateway's /auth/logout) — the
    very next lookup here 404s, no separate blacklist needed.

    Redis is a hard dependency for authentication under this design (there is
    no self-contained fallback once the token carries no claims of its own) —
    a RedisError propagates as AuthError, not a silent allow-through.
    """
    key = f"{SESSION_PREFIX}{token}"
    try:
        raw = await redis_client.get(key)
    except redis.RedisError as exc:
        raise AuthError(f"session store unreachable: {exc}") from exc
    if raw is None:
        raise AuthError("invalid or expired session")
    try:
        blob = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AuthError("corrupt session") from exc

    # DB-tunable (superadmin Settings page), .env fallback — see platform_settings.py.
    # Both idle and absolute are computed fresh on every lookup (not baked in at
    # creation), so a tightened limit applies immediately to sessions already in flight.
    now = int(time.time())
    created_at = int(blob.get("created_at", now))
    absolute_deadline = created_at + platform_settings.session_absolute_hours() * 3600
    if now >= absolute_deadline:
        await redis_client.delete(key)
        raise AuthError("session expired")

    new_expires_at = min(now + platform_settings.session_idle_minutes() * 60, absolute_deadline)
    blob["last_seen_at"] = now
    await redis_client.set(key, json.dumps(blob), ex=max(new_expires_at - now, 1))

    return {
        "user_id": blob["user_id"],
        "username": blob.get("username", ""),
        "role": blob.get("role", "user"),
        "roles": blob.get("roles") or [blob.get("role", "user")],
        # Forced password rotation: while set, main.py locks this session to
        # the change-password endpoint (set for default-seeded credentials).
        "pwc": bool(blob.get("pwc", False)),
        # Login-time MFA gate: while set, main.py locks this session to
        # auth/mfa/verify-login (+ resend/nav) until the code is verified.
        "mfa_pending": bool(blob.get("mfa_pending", False)),
        # Group-enforced MFA gate: no mfa_method yet, but a group requires one —
        # while set, main.py locks this session to the enrollment endpoints
        # (not verify-login, since there's no code to verify yet).
        "mfa_setup_required": bool(blob.get("mfa_setup_required", False)),
        # Server-signed at login (see identity-service auth.py) — forwarded downstream
        # as X-Kiosk-Host-Id so terminal-service can enforce the single-host binding.
        # Never derived from anything client-controlled at this layer.
        "kiosk_host_id": blob.get("kiosk_host_id") if blob.get("kiosk") else None,
    }


async def verify_pat(token: str) -> dict:
    settings = get_settings()
    service_token = mint("api-gateway", "identity-service", settings.service_jwt_secret)

    try:
        resp = await _http.client.post(
            f"{settings.identity_service_url}/api/v1/internal/tokens/verify",
            json={"token": token},
            headers={"X-Service-Token": service_token},
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        raise AuthError(f"identity-service unreachable: {exc}") from exc

    if resp.status_code != 200:
        raise AuthError("token verification failed")

    data = resp.json()
    if not data.get("valid"):
        raise AuthError("invalid or revoked token")

    return {
        "user_id": data["user_id"],
        "username": data.get("username", ""),
        "role": data.get("role", "user"),
        "roles": data.get("roles") or [data.get("role", "user")],
        "scopes": data.get("scopes") or [],
    }


async def resolve_identity(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    if token.startswith("sr_"):
        return await verify_pat(token)
    return await _with_live_roles(await lookup_session(token))


async def resolve_identity_from_bearer_or_cookie(authorization: str | None, cookie_token: str | None) -> dict:
    """ONLY for the GET /auth/session bootstrap endpoint — the one place the
    sr_session cookie is ever consulted. Every other endpoint requires the
    real Bearer token; a same-origin cookie is never accepted for anything
    that mutates state, keeping the ambient-credential surface this
    reintroduces as narrow as possible."""
    if authorization:
        return await resolve_identity(authorization)
    if not cookie_token:
        raise AuthError("missing bearer token")
    return await _with_live_roles(await lookup_session(cookie_token))


async def resolve_identity_from_token(token: str) -> dict:
    """Same as resolve_identity but accepts a raw token string (used by WS proxy
    where browsers cannot send Authorization headers on upgrade requests)."""
    if not token:
        raise AuthError("missing token")
    if token.startswith("sr_"):
        return await verify_pat(token)
    return await _with_live_roles(await lookup_session(token))
