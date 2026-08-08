from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_action
from ..database import get_session
from ..deps import require_admin, require_service_token
from ..models import ZACommandFilter, ZACommandGroup
from ..schemas import CommandFilterCreate, CommandFilterOut, CommandGroupCreate, CommandGroupOut

router = APIRouter(tags=["command-filters"], dependencies=[Depends(require_service_token)])


# ── Command Groups ───────────────────────────────────────────────────────────

@router.get("/command-groups", response_model=list[CommandGroupOut])
async def list_command_groups(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZACommandGroup))
    return result.scalars().all()


@router.post("/command-groups", response_model=CommandGroupOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_command_group(payload: CommandGroupCreate, session: AsyncSession = Depends(get_session)):
    existing = await session.execute(select(ZACommandGroup).where(ZACommandGroup.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="command group name already exists")
    group = ZACommandGroup(**payload.model_dump())
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


@router.put("/command-groups/{group_id}", response_model=CommandGroupOut, dependencies=[Depends(require_admin)])
async def update_command_group(group_id: str, payload: CommandGroupCreate, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZACommandGroup).where(ZACommandGroup.id == group_id))
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="command group not found")
    for field, value in payload.model_dump().items():
        setattr(group, field, value)
    await session.commit()
    await session.refresh(group)
    return group


@router.delete("/command-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_command_group(group_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZACommandGroup).where(ZACommandGroup.id == group_id))
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="command group not found")
    await session.delete(group)
    await session.commit()


# ── Command Filters (SSH ACL) ────────────────────────────────────────────────

@router.get("/command-filters", response_model=list[CommandFilterOut])
async def list_command_filters(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZACommandFilter))
    return result.scalars().all()


@router.post("/command-filters", response_model=CommandFilterOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_command_filter(
    payload: CommandFilterCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    group_result = await session.execute(select(ZACommandGroup).where(ZACommandGroup.id == payload.command_group_id))
    if group_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="command_group_id does not exist")

    cmd_filter = ZACommandFilter(**payload.model_dump())
    session.add(cmd_filter)
    await session.commit()
    await session.refresh(cmd_filter)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="command_filter.create",
        resource_type="command_filter", resource_id=cmd_filter.id, details={"name": cmd_filter.name},
    )
    return cmd_filter


@router.put("/command-filters/{filter_id}", response_model=CommandFilterOut, dependencies=[Depends(require_admin)])
async def update_command_filter(
    filter_id: str,
    payload: CommandFilterCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZACommandFilter).where(ZACommandFilter.id == filter_id))
    cmd_filter = result.scalar_one_or_none()
    if cmd_filter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="command filter not found")

    for field, value in payload.model_dump().items():
        setattr(cmd_filter, field, value)
    await session.commit()
    await session.refresh(cmd_filter)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="command_filter.update",
        resource_type="command_filter", resource_id=cmd_filter.id,
    )
    return cmd_filter


@router.delete("/command-filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_command_filter(
    filter_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZACommandFilter).where(ZACommandFilter.id == filter_id))
    cmd_filter = result.scalar_one_or_none()
    if cmd_filter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="command filter not found")

    await session.delete(cmd_filter)
    await session.commit()

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="command_filter.delete",
        resource_type="command_filter", resource_id=filter_id,
    )
