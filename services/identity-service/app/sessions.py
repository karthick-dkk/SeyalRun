"""Server-side session store (Redis-backed). Replaces the old self-contained
JWT: the browser holds only an opaque, unguessable id — every claim (roles,
kiosk binding, must-change-password) lives here, looked up per request by
api-gateway (see api-gateway/app/security.py::lookup_session), never decoded
client-side. Revocation is `DEL session:<id>` — first-class, not a bolt-on.
"""

from __future__ import annotations

import json
import secrets
import time

import redis.asyncio as redis

SESSION_PREFIX = "session:"
# Single-session-per-user (v1.3, opt-in per user/group — see grouppolicy.py) —
# one pointer per user to their current session id, maintained ONLY while
# enforcement is active for them. Same Redis instance/key convention as
# SESSION_PREFIX; api-gateway's logout handler (main.py) clears this too, so
# the two must agree on the exact string.
USER_SESSION_PREFIX = "user_session:"
# PCI DSS Phase A — time-boxed admin/superadmin JIT elevation (Azure-PIM/sudo-timeout
# style). Per-USER, not per-session: an admin re-proving MFA via /auth/elevate/verify
# unlocks SSH-connect/credential-reveal (for actions with no explicit ZAAuthorization
# grant) across every open tab for that account, for a bounded window. api-gateway
# reads this key directly (security.py) and forwards it downstream as X-Elevated-Until.
ELEVATED_PREFIX = "elevated:"


def _key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def _user_key(user_id: str) -> str:
    return f"{USER_SESSION_PREFIX}{user_id}"


def _elevated_key(user_id: str) -> str:
    return f"{ELEVATED_PREFIX}{user_id}"


async def elevate(client: redis.Redis, user_id: str, minutes: int, reason: str = "") -> int:
    """Grant this user a time-boxed elevated window; returns the unix expiry."""
    until = int(time.time()) + minutes * 60
    await client.set(_elevated_key(user_id), json.dumps({"until": until, "reason": reason}), ex=minutes * 60)
    return until


async def create_session(
    client: redis.Redis,
    *,
    user_id: str,
    username: str,
    role: str,
    roles: list[str],
    idle_minutes: int,
    kiosk_host_id: str | None = None,
    pwc: bool = False,
    mfa_pending: bool = False,
    mfa_setup_required: bool = False,
    single_session_enabled: bool = False,
) -> str:
    """Mint a new session, seeded with a fresh created_at/last_seen_at. Initial
    TTL is the idle window — api-gateway slides it forward on every request,
    capped at session_absolute_hours from created_at (see lookup_session)."""
    session_id = secrets.token_urlsafe(32)
    now = int(time.time())
    blob = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "roles": roles,
        "kiosk": bool(kiosk_host_id),
        "kiosk_host_id": kiosk_host_id,
        "pwc": pwc,
        "mfa_pending": mfa_pending,
        "mfa_setup_required": mfa_setup_required,
        "created_at": now,
        "last_seen_at": now,
    }

    ttl = idle_minutes * 60
    if single_session_enabled:
        # Kick out whatever session this user had open elsewhere before minting
        # the new one, so there's never a window with two simultaneously-valid
        # sessions for the same user. Applies uniformly to every caller
        # (password/SSO login, and the pwc/mfa re-mints) — a re-mint replacing
        # the user's own prior-state session is exactly the intended behavior,
        # not a false positive kick-out of a competing device. Opt-in (v1.3):
        # skipped entirely, no pointer written, for users/groups without it on.
        prior_session_id = await client.get(_user_key(user_id))
        if prior_session_id:
            await client.delete(_key(prior_session_id))
        await client.set(_user_key(user_id), session_id, ex=ttl)

    await client.set(_key(session_id), json.dumps(blob), ex=ttl)
    return session_id
