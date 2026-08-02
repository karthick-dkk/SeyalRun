"""SSH session lifecycle REST endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.elevation import elevation_active
from libs.servicetoken import mint

from ..audit import log_action
from ..config import get_settings
from ..database import get_session
from ..deps import current_user_id, current_username, current_user_role, require_admin, require_service_token
from ..models import ZASSHSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sessions"], dependencies=[Depends(require_service_token)])


class SessionCreate(BaseModel):
    host_id: str
    credential_id: str | None = None
    gateway_id: str | None = None


class SessionOut(BaseModel):
    id: str
    user_id: str
    username: str
    host_id: str
    host_name: str
    credential_id: str
    gateway_id: str | None
    status: str
    client_ip: str
    started_at: datetime
    ended_at: datetime | None
    ws_path: str = ""
    elevation_used: bool = False
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None

    model_config = {"from_attributes": True}


async def _identity_get(path: str, settings=None, **params) -> dict:
    if settings is None:
        settings = get_settings()
    token = mint("terminal-service", "identity-service", settings.service_jwt_secret)
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{settings.identity_service_url}/api/v1{path}",
            headers={"X-Service-Token": token},
            params=params,
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


async def _inventory_get(path: str, settings=None, **params) -> dict | list:
    if settings is None:
        settings = get_settings()
    token = mint("terminal-service", "inventory-service", settings.service_jwt_secret)
    query = {k: v for k, v in params.items() if v is not None}
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(
            f"{settings.inventory_service_url}/api/v1{path}",
            headers={"X-Service-Token": token},
            params=query,
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@router.get("/ssh/credentials")
async def authorized_credentials(
    host_id: str,
    user_id: str = Depends(current_user_id),
):
    """Credentials the caller may connect with on a host (the 'Connect as' picker).
    Resolved purely from PAM authorizations — no role-based bypass. Every caller,
    including superadmin, only sees credentials an explicit authorization grants them
    for this host; SSH access must be earned via authorization, never inherited from role."""
    settings = get_settings()
    out: list[dict] = []
    resolve = await _identity_get("/internal/authz/resolve", settings, user_id=user_id, host_id=host_id)
    actions = resolve.get("actions") or []
    if actions and "ssh" not in actions:
        return []
    ids = resolve.get("credential_ids") or ([resolve["credential_id"]] if resolve.get("credential_id") else [])
    for cid in ids:
        try:
            sec = await _inventory_get(f"/internal/credentials/{cid}/secret", settings)
            out.append({"id": cid, "username": sec.get("username", ""), "name": sec.get("username", "")})
        except HTTPException:
            continue
    return out


@router.post("/ssh/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreate,
    request: Request,
    user_id: str = Depends(current_user_id),
    username: str = Depends(current_username),
    role: str = Depends(current_user_role),
    db: AsyncSession = Depends(get_session),
    elevated_until: str = Header(default="", alias="X-Elevated-Until"),
):
    settings = get_settings()
    client_ip = request.headers.get("x-real-ip", request.client.host if request.client else "")

    # 0. Kiosk binding — a login forced through a Zabbix terminal deep-link is bound to
    # exactly the one host resolved (and server-signed) at login time. This is checked
    # BEFORE authorization: even a host the account is separately authorized for is
    # rejected here if it isn't the bound one. Kiosk never widens access, only narrows.
    kiosk_host_id = request.headers.get("x-kiosk-host-id")
    if kiosk_host_id and payload.host_id != kiosk_host_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="kiosk session is bound to a different host")

    # 1. PAM check — is this host in the user's authorized set? strict=True means this
    # NEVER trusts role alone (no superadmin/admin bypass) — every SSH session, from
    # every entry point, requires a real za_authorizations record for this host,
    # UNLESS the caller is admin/superadmin with an active JIT elevation (PCI DSS
    # Phase A) — a governed, time-boxed, MFA-reproved emergency path instead of a
    # silent role bypass. elevation_used is tagged onto the session row either way,
    # so this is always distinguishable from an ordinary authorized connect.
    authz_data = await _identity_get("/internal/authz/host-ids", settings, user_id=user_id, strict="true")
    allowed_host_ids: list[str] = authz_data.get("host_ids", [])
    elevation_used = False
    if payload.host_id not in allowed_host_ids:
        if role in ("admin", "superadmin") and elevation_active(elevated_until):
            elevation_used = True
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized for this host")

    # 2. Login-ACL check
    now_iso = datetime.now(timezone.utc).isoformat()
    acl = await _identity_get("/internal/login-acls/check", settings, user_id=user_id, ip=client_ip, now_iso=now_iso)
    if not acl.get("allowed", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"login denied by ACL: {acl.get('matched_rule')}")

    # 3. Verify "ssh" action is permitted and resolve/validate the credential — uniformly,
    # regardless of role. An explicitly-supplied credential_id must be one this
    # authorization actually grants; it is never accepted on faith just because the
    # caller happens to know a valid-looking id for this host. Elevation only waives
    # the "must already be pre-authorized" requirement — it does NOT auto-resolve a
    # credential from a grant that doesn't exist, so an elevated admin must name the
    # credential explicitly (payload.credential_id), a deliberate, auditable choice
    # rather than an implicit fallback.
    authz_resolve = await _identity_get("/internal/authz/resolve", settings, user_id=user_id, host_id=payload.host_id)
    actions: list[str] = authz_resolve.get("actions", [])
    if actions and "ssh" not in actions and not elevation_used:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ssh action not permitted for this host")
    authorized_cred_ids: list[str] = authz_resolve.get("credential_ids") or (
        [authz_resolve["credential_id"]] if authz_resolve.get("credential_id") else []
    )
    credential_id = payload.credential_id
    if credential_id:
        if credential_id not in authorized_cred_ids and not elevation_used:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="credential not authorized for this host")
    else:
        credential_id = authorized_cred_ids[0] if authorized_cred_ids else None
    if not credential_id:
        if elevation_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="elevated access requires an explicit credential_id — no authorization exists to auto-resolve one",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no authorized credential for this host — request access in Admin → Authorizations",
        )

    # 4. Fetch host metadata (name, zone_id)
    host_data = await _inventory_get(f"/hosts/{payload.host_id}", settings)
    host_name: str = host_data.get("name", "")
    zone_id: str | None = host_data.get("zone_id")

    # 5. gateway_id itself is no longer read anywhere — the WS handler now derives
    # the ProxyJump chain live at connect time from the host's zone (and that
    # zone's ancestor chain), not from a value frozen into this row. A zone/ancestor
    # with no gateway configured simply contributes no hop, so direct-reachable
    # hosts in an otherwise-gatewayed zone are unaffected. Kept as a column purely
    # so the field still round-trips for any caller still setting it.
    gateway_id = payload.gateway_id

    # 6. Kiosk: same user + same (bound) host — block a second concurrent session,
    # but reuse the same session record if reconnecting within 2 minutes of a drop
    # (a network blip, not a fresh "one-time use" consumption). Different kiosk
    # users on the same host never interact with each other — this query is
    # scoped to user_id, not host_id alone.
    if kiosk_host_id:
        recent = await db.execute(
            select(ZASSHSession)
            .where(ZASSHSession.user_id == user_id, ZASSHSession.host_id == payload.host_id)
            .order_by(ZASSHSession.started_at.desc())
            .limit(1)
        )
        prior = recent.scalar_one_or_none()
        if prior and prior.status in ("active", "pending"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="a kiosk session is already active for this host")
        if (prior and prior.status == "closed" and prior.ended_at
                and datetime.now(timezone.utc) - prior.ended_at < timedelta(minutes=2)):
            prior.credential_id = credential_id
            prior.gateway_id = gateway_id
            prior.client_ip = client_ip
            prior.status = "pending"
            prior.ended_at = None
            await db.commit()
            await db.refresh(prior)
            await log_action(
                user_id=user_id, username=username, action="session.kiosk_reuse",
                resource_type="ssh_session", resource_id=prior.id,
                details={"host_id": payload.host_id, "host_name": host_name},
                ip_address=client_ip, session_id=prior.id, result="success",
            )
            out = SessionOut.model_validate(prior)
            out.ws_path = f"/ws/ssh/{prior.id}"
            return out

    # 7. Create session row (status=pending; WS handler flips it to active on connect)
    session_row = ZASSHSession(
        user_id=user_id,
        username=username,
        host_id=payload.host_id,
        host_name=host_name,
        credential_id=credential_id,
        gateway_id=gateway_id,
        status="pending",
        client_ip=client_ip,
        elevation_used=elevation_used,
    )
    db.add(session_row)
    await db.commit()
    await db.refresh(session_row)

    await log_action(
        user_id=user_id,
        username=username,
        action="session.create",
        resource_type="ssh_session",
        resource_id=session_row.id,
        details={"host_id": payload.host_id, "host_name": host_name, "elevation_used": elevation_used},
        ip_address=client_ip,
        session_id=session_row.id,
        result="success",
    )

    out = SessionOut.model_validate(session_row)
    out.ws_path = f"/ws/ssh/{session_row.id}"
    return out


@router.get("/ssh/sessions", response_model=list[SessionOut])
async def list_sessions(
    status_filter: str | None = None,
    host_id: str | None = None,
    user_id_filter: str | None = None,
    user_id: str = Depends(current_user_id),
    role: str = Depends(current_user_role),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(ZASSHSession)
    if role not in ("admin", "superadmin"):
        stmt = stmt.where(ZASSHSession.user_id == user_id)
    elif user_id_filter:
        stmt = stmt.where(ZASSHSession.user_id == user_id_filter)
    if host_id:
        stmt = stmt.where(ZASSHSession.host_id == host_id)
    if status_filter:
        stmt = stmt.where(ZASSHSession.status == status_filter)
    stmt = stmt.order_by(ZASSHSession.started_at.desc())
    result = await db.execute(stmt)
    rows = result.scalars().all()
    out = []
    for r in rows:
        s = SessionOut.model_validate(r)
        s.ws_path = f"/ws/ssh/{r.id}"
        out.append(s)
    return out


@router.get("/ssh/sessions/{session_id}", response_model=SessionOut)
async def get_session_detail(
    session_id: str,
    user_id: str = Depends(current_user_id),
    role: str = Depends(current_user_role),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(ZASSHSession).where(ZASSHSession.id == session_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    if role not in ("admin", "superadmin") and row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    out = SessionOut.model_validate(row)
    out.ws_path = f"/ws/ssh/{row.id}"
    return out


@router.get("/ssh/sessions/{session_id}/commands")
async def list_session_commands(
    session_id: str,
    user_id: str = Depends(current_user_id),
    role: str = Depends(current_user_role),
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(ZASSHSession).where(ZASSHSession.id == session_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    if role not in ("admin", "superadmin") and row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
    from ..models import ZASessionCommand
    cmd_result = await db.execute(
        select(ZASessionCommand)
        .where(ZASessionCommand.session_id == session_id)
        .order_by(ZASessionCommand.executed_at.asc())
    )
    return [
        {"id": c.id, "command_text": c.command_text, "action": c.action,
         "executed_at": c.executed_at.isoformat() if c.executed_at else None}
        for c in cmd_result.scalars().all()
    ]


@router.delete("/ssh/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def terminate_session(
    session_id: str,
    user_id: str = Depends(current_user_id),
    username: str = Depends(current_username),
    role: str = Depends(current_user_role),
    db: AsyncSession = Depends(get_session),
):
    from ..main import _terminate_events  # late import to avoid circular

    result = await db.execute(select(ZASSHSession).where(ZASSHSession.id == session_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    if role not in ("admin", "superadmin") and row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    row.status = "terminated"
    row.ended_at = datetime.now(timezone.utc)
    await db.commit()

    # Signal the WS handler (if running in this process) to close the connection
    event = _terminate_events.get(session_id)
    if event:
        event.set()

    await log_action(
        user_id=user_id,
        username=username,
        action="session.terminate",
        resource_type="ssh_session",
        resource_id=session_id,
        details={"terminated_by": user_id},
    )


@router.post("/internal/ssh/sessions/terminate-by-user")
async def terminate_sessions_by_user(target_user_id: str, db: AsyncSession = Depends(get_session)):
    """PCI DSS Phase C — called by identity-service's deprovisioning webhook to
    kill every active/pending session for a user being deactivated, immediately.
    Service-token-gated only (router-level) — no X-User-Id trust needed, this
    always acts on the explicit target_user_id, never "the caller"."""
    from ..main import _terminate_events

    result = await db.execute(
        select(ZASSHSession).where(ZASSHSession.user_id == target_user_id, ZASSHSession.status.in_(("active", "pending")))
    )
    rows = result.scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.status = "terminated"
        row.ended_at = now
        event = _terminate_events.get(row.id)
        if event:
            event.set()
    if rows:
        await db.commit()

    for row in rows:
        await log_action(
            user_id=target_user_id, username=row.username, action="session.terminate",
            resource_type="ssh_session", resource_id=row.id,
            details={"terminated_by": "deprovision_webhook"},
        )
    return {"terminated": len(rows)}
