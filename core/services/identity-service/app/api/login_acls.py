from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_action
from ..database import get_session
from ..deps import require_admin, require_service_token
from ..models import ZALoginAcl
from ..schemas import LoginAclCreate, LoginAclOut

router = APIRouter(prefix="/login-acls", tags=["login-acls"], dependencies=[Depends(require_service_token)])


@router.get("", response_model=list[LoginAclOut])
async def list_login_acls(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZALoginAcl))
    return result.scalars().all()


@router.post("", response_model=LoginAclOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_login_acl(
    payload: LoginAclCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    if not payload.user_id and not payload.user_group_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id or user_group_id required")

    acl = ZALoginAcl(**payload.model_dump())
    session.add(acl)
    await session.commit()
    await session.refresh(acl)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="login_acl.create",
        resource_type="login_acl", resource_id=acl.id, details={"name": acl.name},
    )
    return acl


@router.put("/{acl_id}", response_model=LoginAclOut, dependencies=[Depends(require_admin)])
async def update_login_acl(
    acl_id: str,
    payload: LoginAclCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZALoginAcl).where(ZALoginAcl.id == acl_id))
    acl = result.scalar_one_or_none()
    if acl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="login ACL not found")

    for field, value in payload.model_dump().items():
        setattr(acl, field, value)
    await session.commit()
    await session.refresh(acl)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="login_acl.update",
        resource_type="login_acl", resource_id=acl.id,
    )
    return acl


@router.delete("/{acl_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_login_acl(
    acl_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZALoginAcl).where(ZALoginAcl.id == acl_id))
    acl = result.scalar_one_or_none()
    if acl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="login ACL not found")

    await session.delete(acl)
    await session.commit()

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="login_acl.delete",
        resource_type="login_acl", resource_id=acl_id,
    )
