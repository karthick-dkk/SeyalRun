"""Internal endpoints — called by api-gateway / future terminal-service, never exposed to the host."""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_action
from ..config import get_settings
from ..database import get_session
from ..deps import require_service_token
from ..models import (
    ZAApiToken,
    ZAAuditLog,
    ZAAuthorization,
    ZACommandFilter,
    ZACommandGroup,
    ZALoginAcl,
    ZARole,
    ZAUser,
    ZAUserGroupMember,
    ZAUserRole,
)
from ..schemas import TokenVerifyRequest, TokenVerifyResponse
from ..security import verify_pat

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_service_token)])


# ── Many-to-many authorization helpers (v1.1) ────────────────────────────────

async def _user_is_admin(session: AsyncSession, user: ZAUser) -> bool:
    """Effective-role authz-bypass check (group-aware). Only superadmin/admin bypass
    the mandatory za_authorization resource gate; every other role is default-deny."""
    from ..rbacresolve import effective_roles
    from libs.rbaccore import bypasses_authz
    return bypasses_authz(await effective_roles(session, user.id))


def _authz_matches_principal(authz: ZAAuthorization, user_id: str, group_ids: set[str]) -> bool:
    users = set(authz.user_ids or [])
    if authz.user_id:
        users.add(authz.user_id)
    if user_id in users:
        return True
    ugroups = set(authz.user_group_ids or [])
    if authz.user_group_id:
        ugroups.add(authz.user_group_id)
    return bool(ugroups & group_ids)


def _authz_hosts(authz: ZAAuthorization) -> set[str]:
    hs = set(authz.host_ids or [])
    if authz.host_id:
        hs.add(authz.host_id)
    return hs


def _authz_host_groups(authz: ZAAuthorization) -> set[str]:
    hg = set(authz.host_group_ids or [])
    if authz.host_group_id:
        hg.add(authz.host_group_id)
    return hg


class AuditEntry(BaseModel):
    user_id: str | None = None
    username: str = ""
    action: str
    resource_type: str = ""
    resource_id: str = ""
    details: dict = {}
    ip_address: str = ""
    session_id: str | None = None
    result: str | None = None


class DispatchAlertRequest(BaseModel):
    user_id: str
    severity: str = "info"  # info | medium | critical
    subject: str
    body: str


@router.post("/notifications/dispatch-alert")
async def dispatch_alert(payload: DispatchAlertRequest, session: AsyncSession = Depends(get_session)):
    """Called by automation-service after creating a user-targeted in-app
    notification — mails it out too, for any group the user belongs to that
    has policies.notifications_enabled and a min_severity at or below this
    notification's severity. Best-effort: a broken mail config or a bad
    recipient must never surface back to the caller (the in-app notification
    already succeeded independently of this)."""
    from ..grouppolicy import notify_recipients
    from ..mailer import MailError, send_mail

    emails = await notify_recipients(session, payload.user_id, payload.severity)
    sent, failed = [], []
    for addr in emails:
        try:
            await send_mail(session, addr, payload.subject, payload.body)
            sent.append(addr)
        except MailError:
            failed.append(addr)
    return {"sent": sent, "failed": failed}


@router.post("/audit")
async def write_audit(entry: AuditEntry, session: AsyncSession = Depends(get_session)):
    """Allows other services (e.g. inventory-service) to append to the shared audit log."""
    await log_action(
        session,
        user_id=entry.user_id,
        username=entry.username,
        action=entry.action,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        details=entry.details,
        ip_address=entry.ip_address,
        session_id=entry.session_id,
        result=entry.result,
    )
    return {"ok": True}


@router.get("/audit/retention-status")
async def audit_retention_status(days: int | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    """PCI DSS Phase B (10.5.1) — called by automation-service's housekeeping loop
    (audit_log_archive). Reports how many za_audit_logs rows are older than the
    configured retention threshold.

    Deliberately does NOT delete or move rows: this table is a tamper-evident hash
    chain (T9) where every row's entry_hash is derived from its predecessor back to
    GENESIS — removing an old row breaks verify_chain for every row after it, which
    would be a worse regression than the "retention isn't configurable" gap this
    closes. Real hot/cold tiering (copy-not-move to S3/ES, chain intact) is future
    work; this makes the threshold configurable and visible instead of a silent
    hardcoded .env global with no visibility into whether it's actually honored."""
    retention_days = days if days is not None else get_settings().audit_log_retention_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    count = (
        await session.execute(select(func.count()).select_from(ZAAuditLog).where(ZAAuditLog.created_at < cutoff))
    ).scalar_one()
    return {"retention_days": retention_days, "rows_past_retention": count}


@router.get("/audit/daily-summary")
async def audit_daily_summary(session: AsyncSession = Depends(get_session)):
    """PCI DSS Phase B — 'built-in daily summary report generator' item. Called by
    automation-service's housekeeping loop (security_digest_report), which turns
    this into a broadcast notification — feeds the "daily log review" requirement
    even for a deployment with no full SIEM."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await session.execute(
        select(ZAAuditLog.action, func.count())
        .where(ZAAuditLog.created_at >= cutoff)
        .group_by(ZAAuditLog.action)
        .order_by(func.count().desc())
    )
    by_action = {action: count for action, count in result.all()}
    return {"since": cutoff.isoformat(), "total": sum(by_action.values()), "by_action": by_action}


@router.post("/authorizations/sweep-expired")
async def sweep_expired_authorizations(session: AsyncSession = Depends(get_session)):
    """PCI DSS Phase B (7.2.4) — called by automation-service's housekeeping loop
    (authorization_ttl_sweep). Flips enabled=False on any authorization past its
    date_expired that's still marked enabled; "enabled" is the sole flag every
    resolver (authz_resolve, authz_host_ids) checks, so this is the entire
    enforcement point — no downstream change needed."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(ZAAuthorization).where(
            ZAAuthorization.enabled.is_(True),
            ZAAuthorization.date_expired.isnot(None),
            ZAAuthorization.date_expired < now,
        )
    )
    rows = result.scalars().all()
    for authz in rows:
        authz.enabled = False
        authz.status = "expired"
    if rows:
        await session.commit()
    for authz in rows:
        await log_action(
            session, user_id=None, username="system", action="authorization.auto_expired",
            resource_type="authorization", resource_id=authz.id,
            details={"name": authz.name, "date_expired": authz.date_expired.isoformat()},
        )
    return {"expired": len(rows)}


@router.get("/authz/host-ids")
async def authz_host_ids(
    user_id: str = Query(...),
    strict: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
):
    """strict=True skips the superadmin/admin bypass and always resolves real
    za_authorizations records — used by terminal-service so SSH session creation
    can never rely on role alone. Other callers (api-gateway nav gating,
    recording-service visibility) keep the existing bypass by omitting it."""
    user_result = await session.execute(select(ZAUser).where(ZAUser.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        return {"host_ids": [], "host_group_ids": [], "is_admin": False}

    if not strict and await _user_is_admin(session, user):
        return {"host_ids": [], "host_group_ids": [], "is_admin": True}

    group_result = await session.execute(select(ZAUserGroupMember.group_id).where(ZAUserGroupMember.user_id == user_id))
    group_ids = {g for (g,) in group_result.all()}

    now = datetime.now(timezone.utc)
    authz_result = await session.execute(select(ZAAuthorization).where(ZAAuthorization.enabled.is_(True)))

    host_ids: set[str] = set()
    host_group_ids: set[str] = set()
    for authz in authz_result.scalars().all():
        if authz.date_start and authz.date_start > now:
            continue
        if authz.date_expired and authz.date_expired < now:
            continue
        if not _authz_matches_principal(authz, user_id, group_ids):
            continue
        host_ids.update(_authz_hosts(authz))
        host_group_ids.update(_authz_host_groups(authz))

    return {"host_ids": list(host_ids), "host_group_ids": list(host_group_ids), "is_admin": False}


@router.get("/command-filters")
async def internal_command_filters(
    user_id: str = Query(...),
    host_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Resolve applicable command filters for a user (+ optional host) — consumed by terminal-service.

    Returns {filters: [...], default_deny: bool}.  A filter with action="default_deny" acts as a
    global block-all policy marker for that user/host scope: it sets default_deny=True and is
    excluded from the filters list (it has no pattern to match against).
    """
    group_result = await session.execute(select(ZAUserGroupMember.group_id).where(ZAUserGroupMember.user_id == user_id))
    group_ids = [g for (g,) in group_result.all()]

    # A filter with NO user_id/user_group_id set ("Principal: Any" in the UI) is meant
    # to apply to every caller — but SQL's `=` never matches a NULL column, so without
    # this explicit branch such filters were silently never returned to ANYONE (a false
    # sense of security: shown as "Enabled" in the admin UI, enforced for no one).
    conditions = (
        (ZACommandFilter.user_id == user_id)
        | (ZACommandFilter.user_group_id.in_(group_ids or [""]))
        | (ZACommandFilter.user_id.is_(None) & ZACommandFilter.user_group_id.is_(None))
    )
    result = await session.execute(
        select(ZACommandFilter).where(ZACommandFilter.enabled.is_(True), conditions).order_by(ZACommandFilter.priority.desc())
    )
    filters = result.scalars().all()
    if host_id:
        filters = [f for f in filters if f.host_id in (None, host_id)]

    out = []
    default_deny = False
    for f in filters:
        if f.action == "default_deny":
            default_deny = True
            continue  # policy marker — no pattern to evaluate, just sets the fallback mode
        group_result2 = await session.execute(select(ZACommandGroup).where(ZACommandGroup.id == f.command_group_id))
        group = group_result2.scalar_one_or_none()
        out.append(
            {
                "id": f.id,
                "name": f.name,
                "action": f.action,
                "priority": f.priority,
                "patterns": group.patterns if group else [],
                "match_type": group.match_type if group else "regex",
            }
        )
    return {"filters": out, "default_deny": default_deny}


@router.get("/login-acls/check")
async def login_acls_check(
    user_id: str = Query(...),
    ip: str = Query(default=""),
    now_iso: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Evaluate za_login_acls for user_id. Returns {allowed, matched_rule}.
    First matching enabled rule wins (by priority desc); no match → allowed=True."""
    group_result = await session.execute(
        select(ZAUserGroupMember.group_id).where(ZAUserGroupMember.user_id == user_id)
    )
    group_ids = [g for (g,) in group_result.all()]

    # Same NULL-comparison gap as command filters above: a rule with no
    # user_id/user_group_id ("Principal: Any") must still match every caller.
    conditions = (
        (ZALoginAcl.user_id == user_id)
        | (ZALoginAcl.user_group_id.in_(group_ids or [""]))
        | (ZALoginAcl.user_id.is_(None) & ZALoginAcl.user_group_id.is_(None))
    )
    result = await session.execute(
        select(ZALoginAcl)
        .where(ZALoginAcl.enabled.is_(True), conditions)
        .order_by(ZALoginAcl.priority.desc())
    )
    acls = result.scalars().all()

    try:
        now_dt = datetime.fromisoformat(now_iso) if now_iso else datetime.now(timezone.utc)
    except ValueError:
        now_dt = datetime.now(timezone.utc)

    now_time = now_dt.strftime("%H:%M")
    now_dow = now_dt.weekday()  # 0=Monday, 6=Sunday

    for acl in acls:
        if acl.ip_cidr and ip:
            try:
                if ipaddress.ip_address(ip) not in ipaddress.ip_network(acl.ip_cidr, strict=False):
                    continue
            except ValueError:
                continue

        if acl.time_start and acl.time_end:
            if not (acl.time_start <= now_time <= acl.time_end):
                continue

        if acl.days_of_week is not None and len(acl.days_of_week) < 7:
            if now_dow not in acl.days_of_week:
                continue

        return {"allowed": acl.action == "allow", "matched_rule": acl.name}

    return {"allowed": True, "matched_rule": None}


@router.get("/authz/resolve")
async def authz_resolve(
    user_id: str = Query(...),
    host_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Return {credential_id, actions} from the best-matching za_authorizations row
    for (user_id, host_id). Used by terminal-service to default credential and
    verify 'ssh' is in the allowed actions. Returns nulls if no match."""
    group_result = await session.execute(
        select(ZAUserGroupMember.group_id).where(ZAUserGroupMember.user_id == user_id)
    )
    group_ids = {g for (g,) in group_result.all()}

    now = datetime.now(timezone.utc)
    result = await session.execute(select(ZAAuthorization).where(ZAAuthorization.enabled.is_(True)))

    # Rank candidates instead of taking the first direct-host match verbatim: a
    # user is commonly granted SSH via one row (e.g. "ssh action, no specific
    # credential") and a usable credential via a SEPARATE, broader row (e.g. a
    # shared "team -> project hosts" authorization). Query result order across
    # rows is otherwise arbitrary (no ORDER BY), so without ranking, whichever
    # row the DB happens to return first silently shadows a later row that
    # would have actually resolved a credential — surfacing as "authorized,
    # but no credential" even though a valid grant exists. Direct host match
    # always outranks a host-group-only match; within the same specificity, a
    # credential-bearing row always outranks a credential-less one.
    def _rank(authz: ZAAuthorization) -> int:
        direct = host_id in _authz_hosts(authz)
        group = bool(_authz_host_groups(authz))
        has_cred = bool(authz.credential_id or authz.credential_ids)
        if direct and has_cred:
            return 0
        if direct:
            return 1
        if group and has_cred:
            return 2
        return 3  # group without a credential (only reachable when group is True)

    best: ZAAuthorization | None = None
    best_rank = 4
    for authz in result.scalars().all():
        if authz.date_start and authz.date_start > now:
            continue
        if authz.date_expired and authz.date_expired < now:
            continue
        if not _authz_matches_principal(authz, user_id, group_ids):
            continue
        direct = host_id in _authz_hosts(authz)
        group = bool(_authz_host_groups(authz))
        if not direct and not group:
            continue
        rank = _rank(authz)
        if rank < best_rank:
            best, best_rank = authz, rank
            if rank == 0:
                break  # direct host match with a credential — can't do better

    if best is None:
        return {"credential_id": None, "credential_ids": [], "actions": []}
    cred_ids = list(best.credential_ids or ([best.credential_id] if best.credential_id else []))
    return {"credential_id": cred_ids[0] if cred_ids else None, "credential_ids": cred_ids, "actions": best.actions or []}


@router.post("/tokens/verify", response_model=TokenVerifyResponse)
async def verify_token(payload: TokenVerifyRequest, session: AsyncSession = Depends(get_session)):
    if not payload.token.startswith("sr_"):
        return TokenVerifyResponse(valid=False)

    candidates = await session.execute(
        select(ZAApiToken).where(ZAApiToken.token_prefix == payload.token[:9], ZAApiToken.revoked_at.is_(None))
    )
    now = datetime.now(timezone.utc)
    for token in candidates.scalars().all():
        if token.expires_at and token.expires_at < now:
            continue
        if not verify_pat(payload.token, token.token_hash):
            continue

        token.last_used_at = now
        await session.commit()

        user_result = await session.execute(select(ZAUser).where(ZAUser.id == token.user_id))
        user = user_result.scalar_one_or_none()
        if user is None or not user.is_active:
            return TokenVerifyResponse(valid=False)

        # PAT role = effective primary role (group-aware), not the legacy role_id.
        from ..rbacresolve import effective_roles
        from libs.rbaccore import primary_role
        eff = await effective_roles(session, user.id)
        role_name = primary_role(eff) if eff else "user"

        return TokenVerifyResponse(
            valid=True, user_id=user.id, username=user.username, role=role_name, scopes=token.scopes,
        )

    return TokenVerifyResponse(valid=False)


@router.get("/users/{user_id}/roles")
async def internal_user_roles(user_id: str, session: AsyncSession = Depends(get_session)):
    """The user's CURRENT effective role names (direct ∪ group-granted). The api-gateway
    calls this so role/group changes apply without a re-login. "known" lets the gateway
    tell an empty-but-valid user (=[] → deny) from an unreachable identity-service
    (→ stale-JWT fallback) — the removed-from-group access-drop depends on it."""
    from ..rbacresolve import effective_roles
    user = await session.get(ZAUser, user_id)
    if user is None:
        return {"roles": [], "known": False}
    return {"roles": await effective_roles(session, user_id), "known": True}


@router.get("/settings/integration")
async def internal_get_integration(session: AsyncSession = Depends(get_session)):
    """Full Zabbix integration settings INCLUDING the api token — for the gateway
    to consume server-side (never returned to the browser). Service-token gated."""
    from ..models import ZASetting

    row = await session.get(ZASetting, "integration")
    v = dict(row.value) if row and isinstance(row.value, dict) else {}
    return {
        "zabbix_console_url": v.get("zabbix_console_url", ""),
        "zabbix_api_url": v.get("zabbix_api_url", ""),
        "zabbix_api_token": v.get("zabbix_api_token", ""),
    }


@router.get("/settings/platform")
async def internal_get_platform(session: AsyncSession = Depends(get_session)):
    """Platform tunables (rate limits, session timeouts, log level) for api-gateway's
    background settings cache — same DB-first/env-fallback resolution the superadmin
    settings endpoint exposes, just without the require_admin gate (service-token only)."""
    from ..models import ZASetting
    from .settings import PLATFORM_KEY, _PLATFORM_DEFAULTS

    row = await session.get(ZASetting, PLATFORM_KEY)
    v = dict(row.value) if row and isinstance(row.value, dict) else {}
    return {**_PLATFORM_DEFAULTS, **v}


@router.get("/settings/zabbix-module")
async def internal_get_zabbix_module(session: AsyncSession = Depends(get_session)):
    """Zabbix-module trust settings (enabled, trusted IPs, elevated rate limit) for
    api-gateway's background settings cache."""
    from ..models import ZASetting
    from .settings import ZABBIX_MODULE_KEY, _ZABBIX_MODULE_DEFAULTS

    row = await session.get(ZASetting, ZABBIX_MODULE_KEY)
    v = dict(row.value) if row and isinstance(row.value, dict) else {}
    return {**_ZABBIX_MODULE_DEFAULTS, **v}
