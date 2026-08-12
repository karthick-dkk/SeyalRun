from __future__ import annotations

import asyncio
import hashlib
import html as _html
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_action
from ..database import get_session
from ..deps import require_admin, require_service_token
from ..models import ZAAuthorization, ZAAuthorizationApprovalToken, ZAUser
from ..schemas import AuthorizationCreate, AuthorizationOut

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/authorizations",
    tags=["authorizations"],
    dependencies=[Depends(require_service_token)],
)

_EMAIL_TOKEN_LIFETIME_DAYS = 7


def _html_page(title: str, message: str) -> HTMLResponse:
    # Unlike JSON responses (auto-escaped by the SPA's templating), this is raw
    # HTML rendered directly by the clicking browser — admin-set text (authz.name)
    # flows into `message` via authorization_email_action, so it must be escaped
    # here rather than trusted the way in-app admin content elsewhere is.
    title = _html.escape(title)
    message = _html.escape(message)
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,-apple-system,sans-serif;max-width:480px;"
        "margin:96px auto;padding:0 24px;color:#1a1a1a}"
        "h1{font-size:20px;margin-bottom:8px}p{color:#555;line-height:1.5}</style>"
        f"</head><body><h1>{title}</h1><p>{message}</p></body></html>"
    )


async def _approver_candidates(session: AsyncSession, exclude_user_id: str | None) -> list[ZAUser]:
    """Every active user whose effective roles bypass the authz gate (i.e. can
    already call approve_authorization/reject_authorization), excluding the
    requester — the same population require_admin would let through, resolved
    up front so email recipients can never be a superset of who could actually
    act on this via the normal UI. Uses the bulk role resolver (2 queries total)
    rather than looping effective_roles() per active user (1+2N queries) —
    this runs on every authorization create/update."""
    from ..rbacresolve import users_with_bypass_role

    bypass_user_ids = await users_with_bypass_role(session)
    result = await session.execute(select(ZAUser).where(ZAUser.is_active.is_(True)))
    return [
        user for user in result.scalars().all()
        if user.id != exclude_user_id and user.email and user.id in bypass_user_ids
    ]


async def _send_approval_request_emails(session: AsyncSession, authz: ZAAuthorization) -> None:
    """Best-effort: mails every eligible approver (other than the requester) a
    pair of single-use links that approve/reject this specific request without
    requiring login first. Mirrors dispatch_alert's discipline (internal.py) —
    a broken/unconfigured mail setup must never surface back to the caller,
    since the in-app pending_approval state already exists independently."""
    from .settings import _get_value, _PLATFORM_DEFAULTS, PLATFORM_KEY

    try:
        recipients = await _approver_candidates(session, authz.requested_by)
        if not recipients:
            return

        platform = {**_PLATFORM_DEFAULTS, **(await _get_value(session, PLATFORM_KEY))}
        base_url = (platform.get("app_public_url") or "").rstrip("/")

        expires_at = datetime.now(timezone.utc) + timedelta(days=_EMAIL_TOKEN_LIFETIME_DAYS)
        links_by_user: dict[str, dict[str, str]] = {}
        new_tokens = []
        for user in recipients:
            links_by_user[user.id] = {}
            for action in ("approve", "reject"):
                raw_token = secrets.token_urlsafe(32)
                new_tokens.append(ZAAuthorizationApprovalToken(
                    authorization_id=authz.id, approver_user_id=user.id,
                    action=action, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                    expires_at=expires_at,
                ))
                if base_url:
                    links_by_user[user.id][action] = f"{base_url}/api/v1/authorizations/email-action?token={raw_token}"
        session.add_all(new_tokens)
        await session.commit()

        from ..mailer import MailError, get_mail_settings, send_mail_with_settings

        # Resolve mail settings once (the only step that touches `session`), then
        # fire every recipient's send concurrently — send_mail_with_settings never
        # touches `session`, so this is safe. Serially, N recipients meant N full
        # SMTP TCP+TLS+AUTH handshakes back-to-back inside this request handler.
        mail_cfg = await get_mail_settings(session)

        async def _send_one(user) -> None:
            links = links_by_user[user.id]
            lines = [
                "A new access authorization is pending your approval:",
                "",
                f"  Name: {authz.name}",
                f"  Hosts: {', '.join(authz.host_ids or []) or '(none)'}",
                f"  Actions: {', '.join(authz.actions or []) or '(none)'}",
                f"  Expires if approved: {authz.date_expired.isoformat() if authz.date_expired else 'n/a'}",
                "",
            ]
            if links.get("approve") and links.get("reject"):
                lines += [f"Approve: {links['approve']}", f"Reject: {links['reject']}", ""]
            else:
                lines += [
                    "Set 'App Public URL' in Platform Settings to enable one-click approval "
                    "links by email. In the meantime, review this request in the SeyalRun "
                    "console under Authorizations.", "",
                ]
            lines.append(f"This link expires in {_EMAIL_TOKEN_LIFETIME_DAYS} days and can only be used once.")
            try:
                await send_mail_with_settings(mail_cfg, user.email, f"Approval needed: {authz.name}", "\n".join(lines))
            except MailError as exc:
                logger.warning("approval email send failed", extra={"authz_id": authz.id, "to": user.email, "error": str(exc)})

        await asyncio.gather(*(_send_one(user) for user in recipients))
    except Exception:
        logger.exception("approval email dispatch failed", extra={"authz_id": authz.id})


async def _platform_approval_settings(session: AsyncSession) -> tuple[int, list[str]]:
    """(default_ttl_days, approval_exempt_roles) — one platform-settings fetch for
    both, since every _apply_and_require_reapproval call needs both."""
    from .settings import _get_value, _PLATFORM_DEFAULTS, PLATFORM_KEY
    platform = {**_PLATFORM_DEFAULTS, **(await _get_value(session, PLATFORM_KEY))}
    return (
        int(platform.get("authorization_default_ttl_days", 90)),
        list(platform.get("authorization_approval_exempt_roles") or []),
    )


@router.get("", response_model=list[AuthorizationOut])
async def list_authorizations(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAAuthorization))
    return result.scalars().all()


async def _validate_email_token(
    session: AsyncSession, token: str,
) -> tuple[ZAAuthorizationApprovalToken | None, ZAAuthorization | None, ZAUser | None, HTMLResponse | None]:
    """Shared read-only validation for both the GET confirm-page and the POST
    that actually acts. Returns (tok, authz, approver, error_page) — error_page
    is set (and the other three are None/partial) the moment any check fails,
    so both call sites just do `if error: return error`. Re-run in full on
    POST rather than trusting the GET's result — state (another approver
    deciding first, a role change) can change between the two requests."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await session.execute(
        select(ZAAuthorizationApprovalToken).where(ZAAuthorizationApprovalToken.token_hash == token_hash)
    )
    tok = result.scalar_one_or_none()
    if tok is None:
        return None, None, None, _html_page("Link not found", "This approval link is invalid.")
    if tok.used_at is not None:
        return None, None, None, _html_page("Already used", "This approval link has already been used.")
    if tok.expires_at < datetime.now(timezone.utc):
        return None, None, None, _html_page("Link expired", "This approval link has expired — please review the request in the SeyalRun console.")

    authz = await session.get(ZAAuthorization, tok.authorization_id)
    if authz is None:
        return None, None, None, _html_page("Not found", "The related authorization request no longer exists.")
    if authz.status != "pending_approval":
        return None, None, None, _html_page("Already handled", f"This request is already '{authz.status}' — no action was taken.")

    approver = await session.get(ZAUser, tok.approver_user_id)
    if approver is None or not approver.is_active:
        return None, None, None, _html_page("Not authorized", "This approver account is no longer active.")
    from ..rbacresolve import effective_roles
    from libs.rbaccore import bypasses_authz
    if not bypasses_authz(await effective_roles(session, approver.id)):
        return None, None, None, _html_page("Not authorized", "This account no longer has approval privileges.")
    # Belt-and-suspenders: _approver_candidates already excludes the requester
    # at send time, so this should be unreachable, but self-approval must stay
    # blocked even if roles changed between send and click.
    if approver.id == authz.requested_by:
        return None, None, None, _html_page("Not allowed", "The requester cannot approve their own authorization.")

    return tok, authz, approver, None


@router.get("/email-action", response_class=HTMLResponse)
async def authorization_email_action(token: str, session: AsyncSession = Depends(get_session)):
    """Public (unauthenticated) landing page for the approve/reject links sent
    by _send_approval_request_emails — see api-gateway's _PUBLIC_PATHS, which
    is what lets a bare email click reach this without a login session.

    Deliberately side-effect-free: GET must stay safe/idempotent per HTTP
    semantics, and in practice many mail providers and corporate security
    gateways (Outlook Safe Links, Proofpoint, etc.) automatically prefetch
    every URL in an email to scan it — a GET that mutated state would let a
    scanner silently approve/reject a request before any human ever saw the
    message. This renders a confirmation page with a same-token POST form
    instead; only that POST (authorization_email_action_confirm below) burns
    the token and flips the authorization."""
    tok, authz, approver, error = await _validate_email_token(session, token)
    if error is not None:
        return error

    verb = "Approve" if tok.action == "approve" else "Reject"
    escaped_name = _html.escape(authz.name)
    escaped_token = _html.escape(token, quote=True)
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{verb} request</title>"
        "<style>body{font-family:system-ui,-apple-system,sans-serif;max-width:480px;"
        "margin:96px auto;padding:0 24px;color:#1a1a1a}"
        "h1{font-size:20px;margin-bottom:8px}p{color:#555;line-height:1.5}"
        "button{margin-top:16px;padding:10px 20px;font-size:14px;border-radius:6px;border:0;"
        "background:#2563eb;color:#fff;cursor:pointer}</style>"
        f"</head><body><h1>{verb} this request?</h1>"
        f"<p>'{escaped_name}' is waiting on your review. Click below to confirm — this "
        f"page performs no action until you do.</p>"
        # Token travels in the query string (not a hidden form field) so the POST
        # endpoint can read it the same way GET does — no request-body/form-data
        # parsing needed, so no new python-multipart dependency for this alone.
        f"<form method='post' action='email-action?token={escaped_token}'>"
        f"<button type='submit'>{verb}</button></form></body></html>"
    )


@router.post("/email-action", response_class=HTMLResponse)
async def authorization_email_action_confirm(token: str, session: AsyncSession = Depends(get_session)):
    """The actual state-changing step — only reachable via a POST triggered by
    a human clicking the confirm button on the GET page above (or replaying an
    identical POST, which is no more powerful than the button — the token is
    still single-use). See authorization_email_action's docstring for why this
    isn't on GET."""
    tok, authz, approver, error = await _validate_email_token(session, token)
    if error is not None:
        return error

    tok.used_at = datetime.now(timezone.utc)
    # Burn every other still-pending token for this request (both actions, every
    # recipient) so a stale email link can't act on a request someone already decided.
    others = await session.execute(
        select(ZAAuthorizationApprovalToken).where(
            ZAAuthorizationApprovalToken.authorization_id == authz.id,
            ZAAuthorizationApprovalToken.id != tok.id,
            ZAAuthorizationApprovalToken.used_at.is_(None),
        )
    )
    for other in others.scalars().all():
        other.used_at = tok.used_at

    if tok.action == "approve":
        authz.status, authz.enabled = "active", True
    else:
        authz.status, authz.enabled = "rejected", False
    authz.approved_by = approver.id
    authz.approved_at = datetime.now(timezone.utc)
    await session.commit()

    await log_action(
        session, user_id=approver.id, username=approver.username,
        action=f"authorization.{tok.action}_via_email",
        resource_type="authorization", resource_id=authz.id,
        details={"requested_by": authz.requested_by}, result="success",
    )

    verb = "approved" if tok.action == "approve" else "rejected"
    return _html_page(f"Request {verb}", f"'{authz.name}' has been {verb}. You can close this window.")


def _merge(arr: list[str], scalar: str | None) -> list[str]:
    out = list(dict.fromkeys([*(arr or []), *( [scalar] if scalar else [])]))
    return out


def _apply_and_require_reapproval(
    authz: ZAAuthorization, payload: AuthorizationCreate, default_ttl_days: int,
    actor_id: str | None, actor_role: str | None, approval_exempt_roles: list[str],
) -> None:
    """Fold legacy scalars + arrays into the many-to-many arrays (keeping the legacy
    scalar columns populated — first element — for back-compat readers), and reset
    the approval lifecycle in the SAME function rather than a separate one a caller
    could forget to invoke. PCI DSS Phase B segregation of duties: every create/edit
    from a NON-exempt role is inert until a DIFFERENT admin/superadmin approves it
    (see approve_authorization) — "enabled" is the one flag every resolver already
    checks, so this reset is the sole enforcement point; nothing downstream needed
    to change. Deliberately NOT split into two functions (field-apply +
    approval-reset) — a prior version was, and every call site happened to call
    both in sequence, but nothing enforced that coupling; a future call site (bulk
    import, internal sync) could have called just the field-apply half and
    silently reactivated a grant with no re-approval.

    approval_exempt_roles (superadmin-configurable, see api/settings.py) lets a
    deployment decide which roles' own requests skip the approval step entirely.
    Only admin/superadmin can reach this endpoint at all (require_admin gate on
    create/update below), so the only two meaningful values here are "admin" and
    "superadmin" — defaults to superadmin only, so an admin's own request still
    needs a second admin/superadmin to approve it, while superadmin's does not.
    actor_role is read from X-User-Real-Role, not the (possibly write-vouched)
    X-User-Role — api-gateway's downstream_role() vouches any authorized
    non-GET write up to "admin" regardless of the caller's true role (support
    has full CRUD on the authorizations segment), so checking the vouched
    header here would let a support account's request ride along with an
    "admin"-exempt policy the superadmin never intended to cover support."""
    users = _merge(payload.user_ids, payload.user_id)
    ugroups = _merge(payload.user_group_ids, payload.user_group_id)
    hosts = _merge(payload.host_ids, payload.host_id)
    hgroups = _merge(payload.host_group_ids, payload.host_group_id)

    if not users and not ugroups:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="at least one user or user group is required")
    if not hosts and not hgroups:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="at least one host or host group is required")

    authz.name = payload.name
    authz.user_ids, authz.user_group_ids = users, ugroups
    authz.host_ids, authz.host_group_ids = hosts, hgroups
    authz.user_id = users[0] if users else None
    authz.user_group_id = ugroups[0] if ugroups else None
    authz.host_id = hosts[0] if hosts else None
    authz.host_group_id = hgroups[0] if hgroups else None
    creds = _merge(payload.credential_ids, payload.credential_id)
    authz.credential_ids = creds
    authz.credential_id = creds[0] if creds else None
    authz.actions = payload.actions
    authz.date_start = payload.date_start
    # PCI DSS Phase B (7.2.4): a grant left with no expiry by omission is a standing-
    # access gap auditors flag — default one in rather than leaving it open-ended.
    authz.date_expired = payload.date_expired or (datetime.now(timezone.utc) + timedelta(days=default_ttl_days))

    if actor_role in approval_exempt_roles:
        authz.status = "active"
        authz.enabled = True
        authz.requested_by = actor_id
        authz.approved_by = actor_id
        authz.approved_at = datetime.now(timezone.utc)
    else:
        authz.status = "pending_approval"
        authz.enabled = False
        authz.requested_by = actor_id
        authz.approved_by = None
        authz.approved_at = None


@router.post("", response_model=AuthorizationOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_authorization(
    payload: AuthorizationCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    actor_role: str | None = Header(default=None, alias="X-User-Real-Role"),
):
    # Fail closed: a missing actor_id must never leave requested_by empty — that
    # would silently defeat the self-approval check below (empty == empty).
    if not actor_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user identity required")
    default_ttl_days, approval_exempt_roles = await _platform_approval_settings(session)
    authz = ZAAuthorization()
    _apply_and_require_reapproval(authz, payload, default_ttl_days, actor_id, actor_role, approval_exempt_roles)
    session.add(authz)
    await session.commit()
    await session.refresh(authz)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="authorization.create",
        resource_type="authorization", resource_id=authz.id,
        details={"name": authz.name, "auto_approved": authz.status == "active"},
        result="success",
    )
    if authz.status == "pending_approval":
        await _send_approval_request_emails(session, authz)
    return authz


@router.put("/{authz_id}", response_model=AuthorizationOut, dependencies=[Depends(require_admin)])
async def update_authorization(
    authz_id: str,
    payload: AuthorizationCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    actor_role: str | None = Header(default=None, alias="X-User-Real-Role"),
):
    if not actor_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user identity required")
    result = await session.execute(select(ZAAuthorization).where(ZAAuthorization.id == authz_id))
    authz = result.scalar_one_or_none()
    if authz is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="authorization not found")

    # Any edit re-opens approval — otherwise a trivially-approved grant could be
    # silently widened afterward with no second reviewer ever seeing the real change.
    default_ttl_days, approval_exempt_roles = await _platform_approval_settings(session)
    _apply_and_require_reapproval(authz, payload, default_ttl_days, actor_id, actor_role, approval_exempt_roles)

    await session.commit()
    await session.refresh(authz)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="authorization.update",
        resource_type="authorization", resource_id=authz.id,
        details={"auto_approved": authz.status == "active"},
        result="success",
    )
    if authz.status == "pending_approval":
        await _send_approval_request_emails(session, authz)
    return authz


@router.post("/{authz_id}/approve", response_model=AuthorizationOut, dependencies=[Depends(require_admin)])
async def approve_authorization(
    authz_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    if not actor_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user identity required")
    result = await session.execute(select(ZAAuthorization).where(ZAAuthorization.id == authz_id))
    authz = result.scalar_one_or_none()
    if authz is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="authorization not found")
    if authz.status != "pending_approval":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"authorization is {authz.status}, not pending approval")
    if actor_id == authz.requested_by:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="the requester cannot approve their own authorization")

    authz.status = "active"
    authz.enabled = True
    authz.approved_by = actor_id
    authz.approved_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(authz)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="authorization.approve",
        resource_type="authorization", resource_id=authz.id,
        details={"requested_by": authz.requested_by}, result="success",
    )
    return authz


@router.post("/{authz_id}/reject", response_model=AuthorizationOut, dependencies=[Depends(require_admin)])
async def reject_authorization(
    authz_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    if not actor_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user identity required")
    result = await session.execute(select(ZAAuthorization).where(ZAAuthorization.id == authz_id))
    authz = result.scalar_one_or_none()
    if authz is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="authorization not found")
    if authz.status != "pending_approval":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"authorization is {authz.status}, not pending approval")
    # Same segregation-of-duties rule as approve_authorization: a self-reject would
    # let the requester dodge scrutiny by rejecting their own submission the moment
    # a reviewer starts looking at it, then resubmitting a modified version — a
    # different admin must be the one to decide, either way.
    if actor_id == authz.requested_by:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="the requester cannot reject their own authorization")

    authz.status = "rejected"
    authz.enabled = False
    authz.approved_by = actor_id
    authz.approved_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(authz)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="authorization.reject",
        resource_type="authorization", resource_id=authz.id,
        details={"requested_by": authz.requested_by}, result="success",
    )
    return authz


@router.delete("/{authz_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_authorization(
    authz_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZAAuthorization).where(ZAAuthorization.id == authz_id))
    authz = result.scalar_one_or_none()
    if authz is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="authorization not found")

    await session.delete(authz)
    await session.commit()

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="authorization.delete",
        resource_type="authorization", resource_id=authz_id,
        result="success",
    )
