from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_action
from ..config import get_settings
from ..database import get_session
from ..deps import require_service_token
from ..mailer import MailError, send_mail
from ..models import ZARole, ZAUser
from ..plugins.idp.local import LocalIdentityProvider
from ..plugins.idp.zabbix_sso import ZabbixSSOProvider
from ..redis_client import redis_client
from ..sessions import create_session, elevate
from .._pwpolicy import validate_new_password
from ..schemas import (
    ChangePasswordRequest,
    LoginRequest,
    SSOExchangeRequest,
    SSOInitRequest,
    SSOInitResponse,
    TokenResponse,
    UserOut,
)
from ..security import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_service_token)])

_local = LocalIdentityProvider()
_zabbix_sso = ZabbixSSOProvider()

# Highest-privilege first — used to pick the JWT's primary `role`.
_ROLE_PRECEDENCE = ["superadmin", "admin", "automation", "support", "audit", "guest", "user"]


async def _resolve_kiosk_host(kiosk_target: str | None, settings) -> str | None:
    """A raw zbx_host (Zabbix numeric hostid) or host_id (SeyalRun UUID) from a
    terminal deep-link -> a confirmed, real SeyalRun host_id, or None on ANY
    failure (unknown id, malformed input, inventory-service unreachable).
    Never raises — a login must never fail because of this; failure just means
    no kiosk claim gets minted and the login proceeds as an ordinary one.
    Client-side filtering mirrors the established pattern for this kind of
    lookup elsewhere (see zabbix-integration-service webhook.py)."""
    if not kiosk_target:
        return None
    import httpx
    from libs.servicetoken import mint
    try:
        async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=5.0) as client:
            tok = mint("identity-service", "inventory-service", settings.service_jwt_secret)
            resp = await client.get("/api/v1/hosts", headers={"X-Service-Token": tok})
            if resp.status_code != 200:
                return None
            for h in resp.json():
                if h.get("id") == kiosk_target or str(h.get("zabbix_hostid") or "") == kiosk_target:
                    return h["id"]
    except Exception:
        pass
    return None


async def _resolve_roles(session: AsyncSession, user_id: str, fallback_role_id: str | None) -> tuple[list[str], str]:
    """Return (effective role names, primary role): direct roles ∪ group-granted roles.
    Single source of truth is rbacresolve.effective_roles; fallback_role_id is legacy
    (kept for signature compat, unused — group→role plus za_user_roles is authoritative)."""
    from ..rbacresolve import effective_roles
    # A role-less user gets [] (default-deny) — NOT ["user"]. Minting a real capability
    # for an unassigned account means the gateway's identity-down fallback (which trusts
    # the JWT roles) would silently re-grant it. Empty stays empty; enforcement denies.
    names = await effective_roles(session, user_id)
    primary = next((r for r in _ROLE_PRECEDENCE if r in names), names[0] if names else "user")
    return names, primary


async def _user_out(session: AsyncSession, user: ZAUser, kiosk_host_id: str | None = None) -> UserOut:
    role_name = None
    if user.role_id:
        result = await session.execute(select(ZARole).where(ZARole.id == user.role_id))
        role = result.scalar_one_or_none()
        role_name = role.name if role else None
    from ..rbacresolve import effective_roles
    eff = await effective_roles(session, user.id)
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role_id=user.role_id,
        role_name=(eff[0] if eff else role_name),
        roles=eff,
        is_active=user.is_active,
        totp_enabled=user.totp_enabled,
        mfa_method=user.mfa_method,
        allowed_ips=user.allowed_ips or [],
        ip_restriction_enabled=user.ip_restriction_enabled,
        single_session_enabled=user.single_session_enabled,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
        kiosk=bool(kiosk_host_id),
        kiosk_host_id=kiosk_host_id,
    )


# ── Email-OTP store (Redis) — one code per (user, purpose): "login" (the
# login-time MFA gate), "enroll" (setting up email MFA), "disable" and
# "reveal" (re-verifying an existing email-MFA user for a sensitive action).
# TOTP needs none of this — pyotp verifies against the stored secret directly.
_MFA_OTP_TTL = 300  # 5 minutes
_MFA_OTP_MAX_ATTEMPTS = 5
_MFA_OTP_RESEND_COOLDOWN = 30


def _otp_key(user_id: str, purpose: str) -> str:
    return f"mfa_otp:{purpose}:{user_id}"


async def _issue_otp(user: ZAUser, session: AsyncSession, purpose: str) -> None:
    if not user.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="add an email address to your account before using Email OTP")
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = int(time.time())
    blob = {"code": code, "expires_at": now + _MFA_OTP_TTL, "attempts": 0, "sent_at": now}
    await redis_client.set(_otp_key(user.id, purpose), json.dumps(blob), ex=_MFA_OTP_TTL)
    try:
        await send_mail(session, user.email, "Your SeyalRun verification code",
                        f"Your verification code is {code}. It expires in 5 minutes.")
    except MailError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"failed to send verification email: {exc}") from exc


async def _consume_otp(user_id: str, code: str, purpose: str) -> None:
    key = _otp_key(user_id, purpose)
    raw = await redis_client.get(key)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="no verification code pending — request a new one")
    blob = json.loads(raw)
    if int(time.time()) > blob.get("expires_at", 0):
        await redis_client.delete(key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="verification code expired")
    if blob.get("attempts", 0) >= _MFA_OTP_MAX_ATTEMPTS:
        await redis_client.delete(key)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="too many attempts — request a new code")
    if not code or not hmac.compare_digest(code, blob.get("code", "")):
        blob["attempts"] = blob.get("attempts", 0) + 1
        ttl = max(int(blob["expires_at"] - time.time()), 1)
        await redis_client.set(key, json.dumps(blob), ex=ttl)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid code")
    await redis_client.delete(key)


async def _mint_session(
    session: AsyncSession, user: ZAUser, roles: list[str], primary: str, settings,
    *, kiosk_host_id: str | None = None, pwc: bool = False,
) -> tuple[str, bool, bool, bool]:
    """The ONE place every login path (password, SSO, post-password-change
    re-mint) goes through to turn a resolved identity into a session — so the
    mfa_pending claim can never be forgotten on one path while being enforced
    on another. Mirrors create_session's pwc handling: mint immediately with
    the restrictive claim set, rather than withholding the session.

    mfa_setup_required is the group-enforced sibling of mfa_pending: the user
    has NO mfa_method yet, but belongs to a group with policies.mfa_enforced —
    they must complete enrollment (not just verify a code) before anything
    else. Group enforcement is checked only when the user isn't already on
    the mfa_pending path (verifying an existing enrollment already satisfies
    any group's requirement).

    needs_setup_wizard is purely informational (not gateway-enforced — the
    wizard is a friendly first-login nudge, not a security gate): true when
    the effective group policy wants it and the user hasn't been through it."""
    from ..grouppolicy import effective_group_policies

    mfa_pending = bool(user.mfa_method)
    policies = await effective_group_policies(session, user.id)
    mfa_setup_required = bool(policies.get("mfa_enforced")) and not mfa_pending
    needs_setup_wizard = bool(policies.get("setup_wizard")) and not user.setup_completed
    single_session_enabled = bool(user.single_session_enabled) or bool(policies.get("single_session_enabled"))

    token = await create_session(
        redis_client,
        user_id=user.id, username=user.username, role=primary, roles=roles,
        idle_minutes=settings.session_idle_minutes,
        kiosk_host_id=kiosk_host_id,
        pwc=pwc,
        mfa_pending=mfa_pending,
        mfa_setup_required=mfa_setup_required,
        single_session_enabled=single_session_enabled,
    )
    if mfa_pending and user.mfa_method == "email":
        await _issue_otp(user, session, "login")
    return token, mfa_pending, mfa_setup_required, needs_setup_wizard


def _client_ip(request: Request) -> str:
    """The real browser IP, not the raw TCP peer. identity-service sits two hops
    behind the actual client (browser -> edge-proxy -> api-gateway -> here), so
    request.client.host is always api-gateway's own container IP — confirmed
    live (an external login attempt recorded a 172.18.x.x docker-network address
    in the audit log, not the real caller). edge-proxy sets X-Real-IP via nginx's
    `proxy_set_header X-Real-IP $remote_addr` (services/edge-proxy/templates/
    default.conf.template) — REPLACED, not appended, so it can't be spoofed by
    a client-supplied header the way X-Forwarded-For's accumulating list could
    be. api-gateway's proxy.py forwards it through unchanged (not in its
    hop-by-hop strip list). Falls back to the raw peer for direct/non-proxied
    calls (e.g. tests hitting identity-service directly)."""
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    return request.client.host if request.client else ""




@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, session: AsyncSession = Depends(get_session)):
    from .._lockout import attempt_key, check_locked, clear, distinct_username_failures, record_failure

    settings = get_settings()
    ip = _client_ip(request)
    key = attempt_key(payload.username, ip)

    locked_for = await check_locked(session, key)
    if locked_for is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"too many failed attempts; try again in {locked_for}s",
        )

    identity = await _local.authenticate(session=session, username=payload.username, password=payload.password)
    if identity is None:
        await record_failure(
            session, key,
            max_failures=settings.login_max_failures,
            lockout_seconds=settings.login_lockout_seconds,
            window_seconds=settings.login_attempt_window_seconds,
        )
        await log_action(
            session, user_id="", username=payload.username, action="login_failed",
            resource_type="session", ip_address=ip, result="failure",
        )
        # PCI DSS Phase B: distinct usernames failing from one IP is the
        # credential-stuffing/spray signature — a different signal than a single
        # account's repeated failures, which the existing lockout already handles.
        spike = await distinct_username_failures(session, ip, settings.login_attempt_window_seconds)
        if spike >= settings.login_spike_threshold:
            from .._alerts import fire_security_alert
            await fire_security_alert(
                severity="critical", title=f"Login spike from {ip}",
                message=f"{spike} distinct accounts failed login from this IP within the attempt window.",
                source_type="login_spike", source_id=ip,
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid username or password")

    await clear(session, key)  # successful login resets the counter

    result = await session.execute(select(ZAUser).where(ZAUser.id == identity["id"]))
    user = result.scalar_one()

    # Checked AFTER password validation (not before) — a failed-password attempt
    # from a disallowed IP still counts toward lockout exactly as it does today,
    # so this reveals no extra information about IP policy to an attacker who
    # hasn't already proven they know a valid password.
    from ..grouppolicy import ip_login_allowed

    if not await ip_login_allowed(session, user, ip):
        await log_action(
            session, user_id=user.id, username=user.username, action="login_denied_ip",
            resource_type="session", ip_address=ip,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="login not permitted from this IP")

    roles, primary = await _resolve_roles(session, user.id, user.role_id)
    kiosk_host_id = await _resolve_kiosk_host(payload.kiosk_target, settings)
    token, mfa_pending, mfa_setup_required, needs_setup_wizard = await _mint_session(
        session, user, roles, primary, settings,
        kiosk_host_id=kiosk_host_id, pwc=user.must_change_password,
    )

    await log_action(
        session,
        user_id=user.id,
        username=user.username,
        action="login" if not (mfa_pending or mfa_setup_required) else "login_mfa_pending",
        resource_type="session",
        ip_address=ip,
    )

    return TokenResponse(
        access_token=token, user=await _user_out(session, user, kiosk_host_id),
        mfa_required=mfa_pending, mfa_method=user.mfa_method if mfa_pending else None,
        mfa_setup_required=mfa_setup_required, needs_setup_wizard=needs_setup_wizard,
    )


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Self-service password rotation. This is the ONLY endpoint (besides
    /auth/nav) the api-gateway allows while the session carries the
    must-change-password claim, so a default-seeded admin cannot operate the
    console until the shipped password is replaced. Returns a fresh token
    with the claim cleared — no re-login needed."""
    user = await _require_user(session, x_user_id)

    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="current password is incorrect")

    reason = validate_new_password(user.username, payload.current_password, payload.new_password)
    if reason:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    await session.commit()

    settings = get_settings()
    roles, primary = await _resolve_roles(session, user.id, user.role_id)
    kiosk_host_id = await _resolve_kiosk_host(payload.kiosk_target, settings)
    # A forced-password-change user may ALSO have MFA enabled/enforced — re-mint through
    # the same helper as /login so the mfa_pending/mfa_setup_required claim isn't dropped
    # on this path.
    token, mfa_pending, mfa_setup_required, needs_setup_wizard = await _mint_session(session, user, roles, primary, settings, kiosk_host_id=kiosk_host_id)

    await log_action(
        session, user_id=user.id, username=user.username, action="password_change",
        resource_type="user", ip_address=_client_ip(request),
    )

    return TokenResponse(
        access_token=token, user=await _user_out(session, user, kiosk_host_id),
        mfa_required=mfa_pending, mfa_method=user.mfa_method if mfa_pending else None,
        mfa_setup_required=mfa_setup_required, needs_setup_wizard=needs_setup_wizard,
    )


@router.post("/sso-init", response_model=SSOInitResponse)
async def sso_init(payload: SSOInitRequest, request: Request):
    """Called from 127.0.0.1 by the Zabbix module — issues a one-time SSO code."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="sso-init is only callable from localhost")
    code = _zabbix_sso.init_sso(payload.username, payload.zabbix_user_type)
    return SSOInitResponse(sso_code=code)


@router.post("/zbx-sso-init", response_model=SSOInitResponse)
async def zbx_sso_init(request: Request):
    """Called by the Zabbix module's server-side PHP controller, authenticated by an
    HMAC signature over the raw body (X-Zabbix-Module-Signature, mirrors the webhook
    receiver's _authenticate pattern) instead of /auth/sso-init's loopback-only check —
    the module runs inside Zabbix's own container, so it can never be 127.0.0.1 from
    identity-service's point of view. This is the identity-assertion anchor for the
    whole Zabbix-embedded feature: only someone holding ZABBIX_MODULE_SECRET can claim
    "this Zabbix user, at this privilege level" without a prior SeyalRun session."""
    settings = get_settings()
    secret = settings.zabbix_module_secret
    if not secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Zabbix module integration is not configured")
    raw_body = await request.body()
    sig = request.headers.get("X-Zabbix-Module-Signature", "")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid module signature")
    try:
        payload = SSOInitRequest.model_validate_json(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid payload") from exc
    code = _zabbix_sso.init_sso(payload.username, payload.zabbix_user_type)
    return SSOInitResponse(sso_code=code)


@router.post("/sso-exchange", response_model=TokenResponse)
async def sso_exchange(payload: SSOExchangeRequest, request: Request, session: AsyncSession = Depends(get_session)):
    identity = await _zabbix_sso.authenticate(session=session, sso_code=payload.sso_code)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired sso_code")

    result = await session.execute(select(ZAUser).where(ZAUser.id == identity["id"]))
    user = result.scalar_one()

    # Same IP policy as /login — SSO is a parallel credential path (already fixed
    # for the equivalent MFA-bypass gap this session), so it must honor a user's
    # allowed_ips restriction too, not just password login.
    from ..grouppolicy import ip_login_allowed

    ip = _client_ip(request)
    if not await ip_login_allowed(session, user, ip):
        await log_action(
            session, user_id=user.id, username=user.username, action="login_denied_ip",
            resource_type="session", ip_address=ip,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="login not permitted from this IP")

    settings = get_settings()
    roles, primary = await _resolve_roles(session, user.id, user.role_id)
    # Route through the same helper as /login and /change-password — Zabbix SSO is a
    # parallel credential path (a valid sso_code alone used to be enough for a full
    # session), so a user who enabled MFA specifically to require a second factor
    # was still fully bypassable through this endpoint. Mirrors the pwc handling
    # immediately above, which already went through _mint_session for this reason.
    token, mfa_pending, mfa_setup_required, needs_setup_wizard = await _mint_session(session, user, roles, primary, settings, pwc=user.must_change_password)

    await log_action(
        session, user_id=user.id, username=user.username,
        action="login_sso" if not (mfa_pending or mfa_setup_required) else "login_sso_mfa_pending",
        resource_type="session", ip_address=ip,
    )

    return TokenResponse(
        access_token=token, user=await _user_out(session, user),
        mfa_required=mfa_pending, mfa_method=user.mfa_method if mfa_pending else None,
        mfa_setup_required=mfa_setup_required, needs_setup_wizard=needs_setup_wizard,
    )


# ── MFA — TOTP authenticator or email OTP. Gates login (mfa_pending session
# claim, see _mint_session above) AND credential reveal (mfa_verify below). ──

class MfaCodeRequest(BaseModel):
    totp_code: str = ""  # also carries the email-OTP code when method == "email"
    method: str = "totp"  # "totp" | "email" — only read by /mfa/enable
    # Credential the reveal token will be scoped to. The minted token is only
    # valid for this credential id and this user, so a token cannot be replayed
    # to reveal a different credential or by a different user.
    credential_id: str = ""


class MfaVerifyLoginRequest(BaseModel):
    code: str = ""


async def _require_user(session: AsyncSession, user_id: str | None) -> ZAUser:
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    result = await session.execute(select(ZAUser).where(ZAUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return user


@router.get("/mfa/status")
async def mfa_status(
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    user = await _require_user(session, x_user_id)
    return {"enabled": bool(user.mfa_method), "method": user.mfa_method}


@router.post("/mfa/setup")
async def mfa_setup(
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Generate a new TOTP secret (not yet enabled). Returns the secret + otpauth URI for a QR app.
    Gateway-gated on the "mfa" role flag (services/api-gateway/app/rbac.py::is_authorized)."""
    import pyotp
    from ..vault import encrypt

    user = await _require_user(session, x_user_id)
    if user.mfa_method:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="MFA is already enabled — disable it first to switch methods")
    secret = pyotp.random_base32()
    user.totp_secret = encrypt(secret)
    user.totp_enabled = False
    await session.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="SeyalRun")
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/mfa/setup-email")
async def mfa_setup_email(
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Send the first confirmation code for enrolling email-OTP MFA. Gateway-gated
    on the "mfa" role flag, same as /mfa/setup."""
    user = await _require_user(session, x_user_id)
    if user.mfa_method:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="MFA is already enabled — disable it first to switch methods")
    await _issue_otp(user, session, "enroll")
    return {"sent": True}


@router.post("/mfa/enable", response_model=TokenResponse)
async def mfa_enable(
    payload: MfaCodeRequest,
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Enrollment itself is the proof of possession — no separate verify-login
    round trip needed right after. Always re-mints a clean session (mfa_pending=
    False, mfa_setup_required=False explicitly, not via _mint_session's automatic
    recompute, which would immediately flip mfa_pending back to True now that
    mfa_method is set). Harmless/idempotent for the pre-existing voluntary
    self-service enrollment case (that session was never gated to begin with);
    essential for the new group-enforced case, where this is what clears the gate."""
    import pyotp
    from ..vault import decrypt

    user = await _require_user(session, x_user_id)
    if user.mfa_method:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is already enabled")

    if payload.method == "email":
        await _consume_otp(user.id, payload.totp_code, "enroll")
        user.mfa_method = "email"
    else:
        if not user.totp_secret:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="run /auth/mfa/setup first")
        if not pyotp.TOTP(decrypt(user.totp_secret)).verify(payload.totp_code, valid_window=1):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid code")
        user.totp_enabled = True
        user.mfa_method = "totp"

    await session.commit()
    await log_action(session, user_id=user.id, username=user.username, action="mfa.enable",
                     resource_type="user", details={"method": user.mfa_method})

    from ..grouppolicy import effective_group_policies

    settings = get_settings()
    roles, primary = await _resolve_roles(session, user.id, user.role_id)
    # Enrollment may have just satisfied a group's mfa_enforced requirement — check
    # whether the SAME group (or another) also wants the setup wizard shown next,
    # and whether single-session applies (user-level or group-level).
    policies = await effective_group_policies(session, user.id)
    needs_setup_wizard = bool(policies.get("setup_wizard")) and not user.setup_completed
    single_session_enabled = bool(user.single_session_enabled) or bool(policies.get("single_session_enabled"))
    token = await create_session(
        redis_client,
        user_id=user.id, username=user.username, role=primary, roles=roles,
        idle_minutes=settings.session_idle_minutes,
        single_session_enabled=single_session_enabled,
    )
    return TokenResponse(access_token=token, user=await _user_out(session, user), needs_setup_wizard=needs_setup_wizard)


@router.post("/mfa/disable/request-code")
async def mfa_disable_request_code(
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Email-MFA users need a fresh code before /mfa/disable will accept it —
    TOTP users just enter their authenticator's current code directly."""
    user = await _require_user(session, x_user_id)
    if user.mfa_method != "email":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not applicable for this MFA method")
    await _issue_otp(user, session, "disable")
    return {"sent": True}


@router.post("/mfa/disable")
async def mfa_disable(
    payload: MfaCodeRequest,
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Disabling now requires proof of possession (a valid current code) — previously
    any authenticated session could clear MFA with zero verification."""
    import pyotp
    from ..vault import decrypt

    user = await _require_user(session, x_user_id)
    if not user.mfa_method:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled")

    if user.mfa_method == "totp":
        if not user.totp_secret or not pyotp.TOTP(decrypt(user.totp_secret)).verify(payload.totp_code, valid_window=1):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid code")
    else:
        await _consume_otp(user.id, payload.totp_code, "disable")

    user.totp_secret = ""
    user.totp_enabled = False
    user.mfa_method = None
    await session.commit()
    await log_action(session, user_id=user.id, username=user.username, action="mfa.disable", resource_type="user")
    return {"enabled": False}


@router.post("/mfa/verify-login", response_model=TokenResponse)
async def mfa_verify_login(
    payload: MfaVerifyLoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Completes the login-time MFA gate: the session minted by /auth/login (or
    /auth/change-password) carries mfa_pending, and api-gateway blocks every
    path except this one + /auth/mfa/resend + /auth/nav while it's set (see
    api-gateway/app/main.py). On success, mints a clean session with the claim
    cleared — mirrors /auth/change-password's re-mint, no re-login needed."""
    import pyotp
    from ..vault import decrypt

    user = await _require_user(session, x_user_id)
    if not user.mfa_method:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MFA is not enabled for this account")

    if user.mfa_method == "totp":
        if not user.totp_secret or not pyotp.TOTP(decrypt(user.totp_secret)).verify(payload.code, valid_window=1):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid code")
    else:
        await _consume_otp(user.id, payload.code, "login")

    from ..grouppolicy import effective_group_policies

    settings = get_settings()
    roles, primary = await _resolve_roles(session, user.id, user.role_id)
    policies = await effective_group_policies(session, user.id)
    single_session_enabled = bool(user.single_session_enabled) or bool(policies.get("single_session_enabled"))
    token = await create_session(
        redis_client,
        user_id=user.id, username=user.username, role=primary, roles=roles,
        idle_minutes=settings.session_idle_minutes,
        single_session_enabled=single_session_enabled,
    )
    await log_action(
        session, user_id=user.id, username=user.username, action="mfa.verify_login",
        resource_type="session", ip_address=_client_ip(request),
    )
    needs_setup_wizard = bool(policies.get("setup_wizard")) and not user.setup_completed
    return TokenResponse(access_token=token, user=await _user_out(session, user), needs_setup_wizard=needs_setup_wizard)


@router.post("/mfa/resend")
async def mfa_resend(
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Re-sends the login-time email OTP. Rate-limited so a pending session
    can't be used to email-bomb the account's mailbox."""
    user = await _require_user(session, x_user_id)
    if user.mfa_method != "email":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resend is only for the email OTP method")
    raw = await redis_client.get(_otp_key(user.id, "login"))
    if raw:
        blob = json.loads(raw)
        wait = _MFA_OTP_RESEND_COOLDOWN - (int(time.time()) - blob.get("sent_at", 0))
        if wait > 0:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                detail=f"please wait {wait}s before requesting another code")
    await _issue_otp(user, session, "login")
    return {"sent": True}


@router.post("/mfa/reveal/request-code")
async def mfa_reveal_request_code(
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Email-MFA users need a fresh code before /mfa/verify (the credential-reveal
    gate) will accept it — TOTP users just enter their authenticator's current code."""
    user = await _require_user(session, x_user_id)
    if user.mfa_method != "email":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not applicable for this MFA method")
    await _issue_otp(user, session, "reveal")
    return {"sent": True}


@router.post("/mfa/verify")
async def mfa_verify(
    payload: MfaCodeRequest,
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Verify the caller's MFA code and mint a short-lived (30s) reveal token,
    scoped to one credential. Credential reveal is MFA-mandatory: a caller with
    no MFA enabled is rejected outright (previously this silently issued a
    reveal token with zero MFA check when the caller had never enabled TOTP —
    that gap is the reason this endpoint now 403s here instead)."""
    import pyotp
    from ..vault import decrypt

    if not payload.credential_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="credential_id required")

    user = await _require_user(session, x_user_id)
    if not user.mfa_method:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="MFA must be enabled to reveal credentials — enable it in Security settings")

    if user.mfa_method == "totp":
        if not user.totp_secret or not pyotp.TOTP(decrypt(user.totp_secret)).verify(payload.totp_code, valid_window=1):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid code")
    else:
        await _consume_otp(user.id, payload.totp_code, "reveal")

    settings = get_settings()
    now = int(time.time())
    reveal_token = pyjwt.encode(
        {
            "sub": user.id,
            "purpose": "credential_reveal",
            "cid": payload.credential_id,  # token is bound to this single credential
            "iat": now,
            "exp": now + 30,
        },
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )
    return {"valid": True, "reveal_token": reveal_token}


class ElevateVerifyRequest(BaseModel):
    code: str = ""


@router.post("/elevate/request-code")
async def elevate_request_code(
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Email-MFA admins need a fresh code before /elevate/verify will accept it —
    TOTP admins just enter their authenticator's current code. Mirrors
    /mfa/reveal/request-code exactly, scoped to the "elevate" OTP purpose."""
    user = await _require_user(session, x_user_id)
    if user.mfa_method != "email":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="not applicable for this MFA method")
    await _issue_otp(user, session, "elevate")
    return {"sent": True}


@router.post("/elevate/verify")
async def elevate_verify(
    payload: ElevateVerifyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """PCI DSS Phase A — time-boxed admin/superadmin JIT elevation (Azure-PIM/
    sudo-timeout style). Re-proving MFA here unlocks SSH-connect and credential-
    reveal for actions with no explicit ZAAuthorization grant, for a bounded
    window (see terminal-service sessions.py and inventory-service
    credentials.py's elevation fallback checks). Per-user, not per-session —
    api-gateway reads the resulting Redis key directly (security.py) and
    forwards it downstream as X-Elevated-Until."""
    import pyotp

    from .settings import _get_value, _PLATFORM_DEFAULTS, PLATFORM_KEY
    from ..vault import decrypt

    user = await _require_user(session, x_user_id)
    roles, _ = await _resolve_roles(session, user.id, user.role_id)
    if not ({"admin", "superadmin"} & set(roles)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="elevation is only available to admin/superadmin roles")
    if not user.mfa_method:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="MFA must be enabled to use elevated access — enable it in Security settings")

    if user.mfa_method == "totp":
        if not user.totp_secret or not pyotp.TOTP(decrypt(user.totp_secret)).verify(payload.code, valid_window=1):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid code")
    else:
        await _consume_otp(user.id, payload.code, "elevate")

    platform = {**_PLATFORM_DEFAULTS, **(await _get_value(session, PLATFORM_KEY))}
    minutes = int(platform.get("elevated_session_minutes", 30))
    until = await elevate(redis_client, user.id, minutes, reason="mfa_reverify")

    await log_action(
        session, user_id=user.id, username=user.username, action="auth.elevate",
        resource_type="user", resource_id=user.id, ip_address=_client_ip(request),
        details={"minutes": minutes}, result="success",
    )
    return {"elevated_until": until, "elevated_minutes": minutes}


@router.post("/setup/complete")
async def setup_complete(
    session: AsyncSession = Depends(get_session),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    """Dismisses the first-login setup wizard for good — self-only, no extra
    auth beyond being authenticated (the session reaching here is already a
    normal, fully-authorized one: mfa_setup_required is always cleared before
    the frontend ever shows the wizard's summary step)."""
    user = await _require_user(session, x_user_id)
    user.setup_completed = True
    await session.commit()
    return {"setup_completed": True}


# ── Link token — short-lived deep-link for Zabbix integration ────────────────

class LinkTokenRequest(BaseModel):
    action: str = "terminal"        # terminal | playbook
    host_id: str | None = None      # SeyalRun host UUID
    zbx_hostid: str | None = None   # Zabbix hostid (terminal deep-link)
    template_id: str | None = None  # job template id (playbook deep-link)


class LinkTokenResponse(BaseModel):
    token: str
    url: str
    expires_in: int = 600


@router.post("/link-token", response_model=LinkTokenResponse)
async def create_link_token(
    payload: LinkTokenRequest,
    request: Request,
    x_user_id:   str | None = Header(default=None, alias="X-User-Id"),
    x_user_name: str | None = Header(default=None, alias="X-User-Name"),
    x_user_role: str | None = Header(default=None, alias="X-User-Role"),
):
    """Generate a 10-minute deep-link JWT for Zabbix → SeyalRun navigation.

    The URL can be embedded in a Zabbix Action, host custom link, or map
    element so clicking it opens the SSH terminal or job page pre-targeted to
    the host — no manual SeyalRun navigation required.

    The client decodes (not verifies) the JWT payload to read target params.
    The server enforces authorization when the SSH session or job is created.
    """
    if not x_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    settings = get_settings()
    now = int(time.time())

    claims: dict = {
        "sub":      x_user_id,
        "username": x_user_name or "",
        "role":     x_user_role or "user",
        "lt":       True,
        "action":   payload.action,
        "iat":      now,
        "exp":      now + 600,
    }
    if payload.host_id:     claims["host_id"]     = payload.host_id
    if payload.zbx_hostid:  claims["zbx_hostid"]  = payload.zbx_hostid
    if payload.template_id: claims["template_id"] = payload.template_id

    token = pyjwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    origin = settings.frontend_origin or (
        f"{request.headers.get('x-forwarded-proto', 'https')}://{request.headers.get('host', 'localhost')}"
    )
    if payload.action == "terminal":
        parts = []
        if payload.host_id:    parts.append(f"host_id={payload.host_id}")
        if payload.zbx_hostid: parts.append(f"zbx_host={payload.zbx_hostid}")
        parts.append(f"lt={token}&autoconnect=1")
        url = f"{origin}/#/terminal?{'&'.join(parts)}"
    else:
        url = f"{origin}/#/jobs/run?template={payload.template_id or ''}&lt={token}"

    return LinkTokenResponse(token=token, url=url)
