"""/api/v1/auth/access-requests — a user asks for a login on a host they can see.

Self-service and self-scoped, like /auth/my-access: the requester is the
gateway-set X-User-Id and nothing else, so nobody can request, list or cancel on
someone else's behalf. Under /auth because that segment is open to every
signed-in role (api-gateway rbac._ALWAYS); PATs are refused there (no token
scope maps to "auth"), so an agent cannot file requests in its owner's name.

Note on headers: for a non-GET under /auth the gateway VOUCHES X-User-Role up to
"admin" (rbac.downstream_role). Nothing here reads a role header — the only role
question ("does this caller see every host?") is answered from the database.

A request becomes a za_authorizations row (request_kind = "user_request",
status pending_approval, enabled False), approved or rejected in the existing
Approvals → Access rules flow. app/accessrequest.py holds what both sides share.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.servicetoken import mint

from ..accessrequest import DURATIONS, REQUEST_ACTIONS, USER_REQUEST, clean_reason, display_status
from ..audit import log_action
from ..config import get_settings
from ..database import get_session
from ..deps import current_user_id, require_service_token
from ..models import ZAAuthorization, ZAUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/access-requests", tags=["access-requests"], dependencies=[Depends(require_service_token)])

# Mirrors api-gateway rbac._SEE_ALL_HOSTS: roles whose host LIST is unfiltered.
# Resolved from the DB (effective roles), never from a header.
_SEE_ALL_HOSTS = frozenset({"superadmin", "admin", "support"})


class AccessRequestCreate(BaseModel):
    host_id: str = Field(min_length=1, max_length=36)
    credential_id: str = Field(min_length=1, max_length=36)
    duration: Literal["1h", "8h", "1d", "7d"]
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        # Printable text and line breaks only (app/accessrequest.py) — the reason
        # is shown to approvers, in the console and by email.
        return clean_reason(v)


# ── helpers ──────────────────────────────────────────────────────────────────

async def _host_accounts(host_id: str) -> dict:
    """{host: {id, name}, accounts: [{id, name, username}]} from inventory.

    Fails CLOSED: if inventory cannot say which accounts belong to the host, no
    request is accepted (a request naming an account we could not verify would
    reach an approver as if it had been checked)."""
    settings = get_settings()
    token = mint("identity-service", "inventory-service", settings.service_jwt_secret)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.inventory_service_url}/api/v1/internal/hosts/{host_id}/accounts",
                headers={"X-Service-Token": token},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="inventory unavailable") from exc
    if resp.status_code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    if resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="inventory unavailable")
    data = resp.json()
    accounts = [
        {"id": a["id"], "name": a.get("name") or a.get("username") or "", "username": a.get("username") or ""}
        for a in (data.get("accounts") or [])
    ]
    return {"host": {"id": host_id, "name": (data.get("host") or {}).get("name") or ""}, "accounts": accounts}


async def can_see_host(session: AsyncSession, user_id: str, host_id: str, kiosk_host_id: str | None = None) -> bool:
    """The same answer the Hosts page gives: role-wide visibility, or the host is
    in the caller's authz/host-ids WITH host-tree view membership included."""
    if kiosk_host_id and kiosk_host_id != host_id:
        return False
    from ..rbacresolve import effective_roles
    from .internal import authz_host_ids

    if set(await effective_roles(session, user_id)) & _SEE_ALL_HOSTS:
        return True
    data = await authz_host_ids(user_id=user_id, strict=False, include_view=True, session=session)
    return bool(data.get("is_admin")) or host_id in set(data.get("host_ids") or [])


async def has_login(session: AsyncSession, user_id: str, host_id: str) -> bool:
    """Whether the caller can already open a session on this host — the same test
    terminal-service's connectable-hosts list applies to authz/resolve."""
    from .internal import authz_resolve

    r = await authz_resolve(user_id=user_id, host_id=host_id, session=session)
    actions = r.get("actions") or []
    creds = r.get("credential_ids") or ([r["credential_id"]] if r.get("credential_id") else [])
    return (not actions or "ssh" in actions) and bool(creds or "manual_account" in actions)


async def _pending(session: AsyncSession, user_id: str, host_id: str) -> list[ZAAuthorization]:
    rows = await session.execute(
        select(ZAAuthorization).where(
            ZAAuthorization.request_kind == USER_REQUEST,
            ZAAuthorization.requested_by == user_id,
            ZAAuthorization.status == "pending_approval",
            ZAAuthorization.host_id == host_id,
        )
    )
    return list(rows.scalars().all())


def _out(a: ZAAuthorization, now: datetime) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "host_id": a.host_id,
        "credential_id": a.credential_id,
        "reason": a.reason,
        "duration_minutes": a.request_duration_minutes,
        "status": display_status(a, now),
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "approved_at": a.approved_at.isoformat() if a.approved_at else None,
        "date_start": a.date_start.isoformat() if a.date_start else None,
        "date_expired": a.date_expired.isoformat() if a.date_expired else None,
    }


# ── endpoints ────────────────────────────────────────────────────────────────

@router.get("")
async def list_my_requests(
    user_id: str = Depends(current_user_id),
    session: AsyncSession = Depends(get_session),
):
    rows = await session.execute(
        select(ZAAuthorization)
        .where(ZAAuthorization.request_kind == USER_REQUEST, ZAAuthorization.requested_by == user_id)
        .order_by(ZAAuthorization.created_at.desc())
        .limit(200)
    )
    now = datetime.now(timezone.utc)
    return {"items": [_out(a, now) for a in rows.scalars().all()]}


@router.get("/options")
async def request_options(
    host_id: str = Query(..., min_length=1, max_length=36),
    user_id: str = Depends(current_user_id),
    kiosk_host_id: str | None = Header(default=None, alias="X-Kiosk-Host-Id"),
    session: AsyncSession = Depends(get_session),
):
    """What the request dialog offers for one host: its accounts (id, name,
    username — never a secret) and the allowed durations. 404 for a host the
    caller cannot see, the same answer as for one that does not exist."""
    if not await can_see_host(session, user_id, host_id, kiosk_host_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    inv = await _host_accounts(host_id)
    pending = {a.credential_id for a in await _pending(session, user_id, host_id)}
    return {
        "host": inv["host"],
        "accounts": [{**a, "pending": a["id"] in pending} for a in inv["accounts"]],
        "durations": [{"value": k, "minutes": m, "label": label} for k, (m, label) in DURATIONS.items()],
        "has_access": await has_login(session, user_id, host_id),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_request(
    payload: AccessRequestCreate,
    user_id: str = Depends(current_user_id),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    kiosk_host_id: str | None = Header(default=None, alias="X-Kiosk-Host-Id"),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(ZAUser, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user identity required")
    if not await can_see_host(session, user_id, payload.host_id, kiosk_host_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    inv = await _host_accounts(payload.host_id)
    account = next((a for a in inv["accounts"] if a["id"] == payload.credential_id), None)
    if account is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="that account does not belong to this host")
    if await has_login(session, user_id, payload.host_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="you already have login access to this host")
    if any(a.credential_id == payload.credential_id for a in await _pending(session, user_id, payload.host_id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="you already have a pending request for this account on this host",
        )

    minutes, label = DURATIONS[payload.duration]
    now = datetime.now(timezone.utc)

    host_name = inv["host"]["name"] or payload.host_id
    authz = ZAAuthorization(
        name=f"Access request: {user.username} → {host_name} as {account['username']} ({label})"[:200],
        user_id=user_id, user_ids=[user_id], user_group_id=None, user_group_ids=[],
        host_id=payload.host_id, host_ids=[payload.host_id], host_group_id=None, host_group_ids=[],
        credential_id=payload.credential_id, credential_ids=[payload.credential_id],
        actions=list(REQUEST_ACTIONS),
        date_start=now, date_expired=now + timedelta(minutes=minutes),
        # Always pending, always inert, whoever asks — no exempt role, no policy
        # switch. Activation is approve_authorization's job, by someone else.
        status="pending_approval", enabled=False,
        requested_by=user_id, approved_by=None, approved_at=None,
        request_kind=USER_REQUEST, reason=payload.reason, request_duration_minutes=minutes,
    )
    session.add(authz)
    await session.commit()
    await session.refresh(authz)

    await log_action(
        session, user_id=user_id, username=actor_name or user.username, action="access_request.create",
        resource_type="authorization", resource_id=authz.id,
        details={"host_id": payload.host_id, "host_name": host_name, "credential_id": payload.credential_id,
                 "account": account["username"], "duration": payload.duration, "reason": payload.reason},
        result="success",
    )
    from .authorizations import _send_approval_request_emails

    await _send_approval_request_emails(session, authz)
    return _out(authz, now)


@router.post("/{request_id}/cancel")
async def cancel_request(
    request_id: str,
    user_id: str = Depends(current_user_id),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    session: AsyncSession = Depends(get_session),
):
    authz = await session.get(ZAAuthorization, request_id)
    # Someone else's request, or an admin-written rule, is "not found" — not
    # "forbidden", which would confirm the id exists.
    if authz is None or authz.request_kind != USER_REQUEST or authz.requested_by != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="access request not found")
    if authz.status != "pending_approval":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"access request is {authz.status}, not pending")
    authz.status = "cancelled"
    authz.enabled = False
    await session.commit()
    await session.refresh(authz)
    await log_action(
        session, user_id=user_id, username=actor_name or "", action="access_request.cancel",
        resource_type="authorization", resource_id=authz.id,
        details={"host_id": authz.host_id, "credential_id": authz.credential_id}, result="success",
    )
    return _out(authz, datetime.now(timezone.utc))
