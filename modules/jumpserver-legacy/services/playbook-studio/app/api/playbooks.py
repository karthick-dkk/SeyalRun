"""Playbook CRUD API — create, read, update, delete, YAML preview, validate."""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_async_session
from ..dependencies import get_current_user
from ..models.domain import Playbook
from ..models.schemas import (
    PlaybookCreate,
    PlaybookListResponse,
    PlaybookResponse,
    PlaybookUpdate,
)
from ..services.playbook_builder import build_yaml, validate_task_definitions

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/playbooks", tags=["playbooks"])


def _to_response(p: Playbook) -> PlaybookResponse:
    return PlaybookResponse(
        id=p.id,
        name=p.name,
        description=p.description,
        category=p.category,
        tags=p.tags or [],
        tasks=p.tasks or [],
        variables=p.variables or [],
        is_template=p.is_template,
        source_template_id=p.source_template_id,
        created_by=p.created_by,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("", response_model=PlaybookListResponse)
async def list_playbooks(
    category: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> PlaybookListResponse:
    q = select(Playbook).where(Playbook.is_template == False)  # noqa: E712
    if category:
        q = q.where(Playbook.category == category)
    if search:
        q = q.where(Playbook.name.ilike(f"%{search}%"))

    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    q = q.order_by(Playbook.updated_at.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    playbooks = result.scalars().all()

    return PlaybookListResponse(total=total, items=[_to_response(p) for p in playbooks])


@router.post("", response_model=PlaybookResponse, status_code=status.HTTP_201_CREATED)
async def create_playbook(
    body: PlaybookCreate,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> PlaybookResponse:
    from ..models.schemas import TaskDefinition, VariableDefinition

    tasks = [TaskDefinition(**t) if isinstance(t, dict) else t for t in body.tasks]
    variables = [VariableDefinition(**v) if isinstance(v, dict) else v for v in body.variables]

    yaml_cache = build_yaml(
        playbook_name=body.name,
        tasks=tasks,
        variables=variables,
        description=body.description,
    )

    p = Playbook(
        name=body.name,
        description=body.description,
        category=body.category,
        tags=body.tags,
        tasks=[t.model_dump() for t in tasks],
        variables=[v.model_dump() for v in variables],
        yaml_cache=yaml_cache,
        created_by=user["username"],
    )
    db.add(p)
    await db.flush()
    log.info("playbook_created", id=str(p.id), name=p.name, user=user["username"])
    return _to_response(p)


@router.get("/{playbook_id}", response_model=PlaybookResponse)
async def get_playbook(
    playbook_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> PlaybookResponse:
    p = await db.get(Playbook, playbook_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")
    return _to_response(p)


@router.put("/{playbook_id}", response_model=PlaybookResponse)
async def update_playbook(
    playbook_id: uuid.UUID,
    body: PlaybookCreate,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> PlaybookResponse:
    p = await db.get(Playbook, playbook_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")

    from ..models.schemas import TaskDefinition, VariableDefinition

    tasks = [TaskDefinition(**t) if isinstance(t, dict) else t for t in body.tasks]
    variables = [VariableDefinition(**v) if isinstance(v, dict) else v for v in body.variables]

    p.name = body.name
    p.description = body.description
    p.category = body.category
    p.tags = body.tags
    p.tasks = [t.model_dump() for t in tasks]
    p.variables = [v.model_dump() for v in variables]
    p.yaml_cache = build_yaml(playbook_name=body.name, tasks=tasks, variables=variables)

    await db.flush()
    return _to_response(p)


@router.patch("/{playbook_id}", response_model=PlaybookResponse)
async def patch_playbook(
    playbook_id: uuid.UUID,
    body: PlaybookUpdate,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> PlaybookResponse:
    p = await db.get(Playbook, playbook_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")

    if body.name is not None:
        p.name = body.name
    if body.description is not None:
        p.description = body.description
    if body.category is not None:
        p.category = body.category
    if body.tags is not None:
        p.tags = body.tags
    if body.tasks is not None:
        p.tasks = [t.model_dump() for t in body.tasks]
    if body.variables is not None:
        p.variables = [v.model_dump() for v in body.variables]

    # Rebuild YAML cache
    from ..models.schemas import TaskDefinition, VariableDefinition

    tasks = [TaskDefinition(**t) if isinstance(t, dict) else t for t in (p.tasks or [])]
    variables = [VariableDefinition(**v) if isinstance(v, dict) else v for v in (p.variables or [])]
    p.yaml_cache = build_yaml(playbook_name=p.name, tasks=tasks, variables=variables)

    await db.flush()
    return _to_response(p)


@router.delete("/{playbook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playbook(
    playbook_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> None:
    p = await db.get(Playbook, playbook_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")
    await db.delete(p)


@router.get("/{playbook_id}/yaml")
async def get_playbook_yaml(
    playbook_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Return the rendered YAML for a playbook (regenerates if cache is empty)."""
    p = await db.get(Playbook, playbook_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")

    if not p.yaml_cache:
        from ..models.schemas import TaskDefinition, VariableDefinition

        tasks = [TaskDefinition(**t) if isinstance(t, dict) else t for t in (p.tasks or [])]
        variables = [
            VariableDefinition(**v) if isinstance(v, dict) else v for v in (p.variables or [])
        ]
        p.yaml_cache = build_yaml(playbook_name=p.name, tasks=tasks, variables=variables)
        await db.flush()

    return {"playbook_id": str(playbook_id), "yaml": p.yaml_cache}


@router.post("/{playbook_id}/validate")
async def validate_playbook(
    playbook_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Validate playbook task definitions and return warnings."""
    p = await db.get(Playbook, playbook_id)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")

    from ..models.schemas import TaskDefinition

    tasks = [TaskDefinition(**t) if isinstance(t, dict) else t for t in (p.tasks or [])]
    warnings = validate_task_definitions(tasks)

    return {
        "playbook_id": str(playbook_id),
        "valid": len(warnings) == 0,
        "warnings": warnings,
        "task_count": len(tasks),
    }
