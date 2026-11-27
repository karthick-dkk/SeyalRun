"""/api/v1/authorizations/host-group-viewers — host tree VIEW membership.

An admin adds users and user groups to a node of the host tree (a host group).
They then see every host under that node — Hosts page, Posture — and can ask
for access to one (api/access_requests.py), but cannot log in, transfer files,
reveal credentials or run anything: those all resolve za_authorizations, and
these rows live in their own table (models.ZAHostGroupViewer) that only
authz/host-ids?include_view=true reads.

Active immediately — it grants visibility, not access, so there is nothing for a
second admin to approve. Managed by admin/superadmin only, checked on the REAL
role: the gateway vouches any authorized write on the "authorizations" segment
up to X-User-Role: admin (support has write there), and support adding viewers
was not asked for.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.servicetoken import mint

from ..audit import log_action
from ..config import get_settings
from ..database import get_session
from ..deps import require_service_token
from ..models import ZAHostGroupViewer, ZAUser, ZAUserGroup

router = APIRouter(
    prefix="/authorizations/host-group-viewers",
    tags=["host-tree"],
    dependencies=[Depends(require_service_token)],
)


async def require_real_admin(
    real_role: str | None = Header(default=None, alias="X-User-Real-Role"),
    role: str | None = Header(default=None, alias="X-User-Role"),
) -> None:
    # Real role when the gateway sent one; otherwise the plain role (internal callers).
    effective = real_role if real_role is not None else role
    if effective not in ("admin", "superadmin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")


class ViewerAdd(BaseModel):
    host_group_id: str = Field(min_length=1, max_length=36)
    user_ids: list[str] = Field(default_factory=list, max_length=500)
    user_group_ids: list[str] = Field(default_factory=list, max_length=500)


def _out(v: ZAHostGroupViewer) -> dict:
    return {
        "id": v.id, "host_group_id": v.host_group_id, "user_id": v.user_id, "user_group_id": v.user_group_id,
        "created_by": v.created_by, "created_at": v.created_at.isoformat() if v.created_at else None,
    }


async def _host_group_exists(group_id: str) -> bool:
    settings = get_settings()
    token = mint("identity-service", "inventory-service", settings.service_jwt_secret)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.inventory_service_url}/api/v1/host-groups/{group_id}",
                headers={"X-Service-Token": token},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="inventory unavailable") from exc
    if resp.status_code == 404:
        return False
    if resp.status_code != 200:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="inventory unavailable")
    return True


@router.get("", dependencies=[Depends(require_real_admin)])
async def list_viewers(
    host_group_id: str | None = Query(default=None, max_length=36),
    session: AsyncSession = Depends(get_session),
):
    q = select(ZAHostGroupViewer).order_by(ZAHostGroupViewer.created_at)
    if host_group_id:
        q = q.where(ZAHostGroupViewer.host_group_id == host_group_id)
    return [_out(v) for v in (await session.execute(q)).scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_real_admin)])
async def add_viewers(
    payload: ViewerAdd,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    user_ids = list(dict.fromkeys(u for u in payload.user_ids if u))
    group_ids = list(dict.fromkeys(g for g in payload.user_group_ids if g))
    if not user_ids and not group_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="at least one user or user group is required")
    if not await _host_group_exists(payload.host_group_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host group not found")
    if user_ids:
        known = {u for (u,) in (await session.execute(select(ZAUser.id).where(ZAUser.id.in_(user_ids)))).all()}
        if set(user_ids) - known:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown user id(s)")
    if group_ids:
        known = {g for (g,) in (await session.execute(select(ZAUserGroup.id).where(ZAUserGroup.id.in_(group_ids)))).all()}
        if set(group_ids) - known:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown user group id(s)")

    existing = (await session.execute(
        select(ZAHostGroupViewer).where(ZAHostGroupViewer.host_group_id == payload.host_group_id)
    )).scalars().all()
    have_users = {v.user_id for v in existing if v.user_id}
    have_groups = {v.user_group_id for v in existing if v.user_group_id}
    created = [
        *(ZAHostGroupViewer(host_group_id=payload.host_group_id, user_id=u, created_by=actor_id)
          for u in user_ids if u not in have_users),
        *(ZAHostGroupViewer(host_group_id=payload.host_group_id, user_group_id=g, created_by=actor_id)
          for g in group_ids if g not in have_groups),
    ]
    session.add_all(created)
    await session.commit()
    for v in created:
        await session.refresh(v)
        await log_action(
            session, user_id=actor_id, username=actor_name or "", action="host_group_viewer.add",
            resource_type="host_group", resource_id=payload.host_group_id,
            details={"viewer_id": v.id, "user_id": v.user_id, "user_group_id": v.user_group_id, "access": "view"},
            result="success",
        )
    return [_out(v) for v in created]


@router.delete("/{viewer_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_real_admin)])
async def remove_viewer(
    viewer_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    v = await session.get(ZAHostGroupViewer, viewer_id)
    if v is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="viewer not found")
    details = {"viewer_id": v.id, "user_id": v.user_id, "user_group_id": v.user_group_id}
    group_id = v.host_group_id
    await session.delete(v)
    await session.commit()
    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="host_group_viewer.remove",
        resource_type="host_group", resource_id=group_id, details=details, result="success",
    )
