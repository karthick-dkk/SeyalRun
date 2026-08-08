from __future__ import annotations

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.deps import require_service_token, get_user_role
from app.models import ZAProject

router = APIRouter(dependencies=[Depends(require_service_token)])


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    source_type: str = "local"
    git_url: str = ""
    git_branch: str = "main"


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


@router.get("/projects")
async def list_projects(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAProject).order_by(ZAProject.created_at.desc()))
    return [_out(p) for p in result.scalars().all()]


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    role: str = Depends(get_user_role),
    session: AsyncSession = Depends(get_session),
):
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")
    proj = ZAProject(**payload.model_dump())
    session.add(proj)
    await session.commit()
    await session.refresh(proj)
    return _out(proj)


@router.get("/projects/{project_id}")
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)):
    proj = await session.get(ZAProject, project_id)
    if proj is None:
        raise HTTPException(status_code=404)
    return _out(proj)


@router.put("/projects/{project_id}")
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    role: str = Depends(get_user_role),
    session: AsyncSession = Depends(get_session),
):
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")
    proj = await session.get(ZAProject, project_id)
    if proj is None:
        raise HTTPException(status_code=404)
    for k, v in payload.model_dump(exclude_none=True).items():
        setattr(proj, k, v)
    proj.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(proj)
    return _out(proj)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    role: str = Depends(get_user_role),
    session: AsyncSession = Depends(get_session),
):
    if role not in ("superadmin", "admin", "automation"):
        raise HTTPException(status_code=403, detail="automation write access required")
    proj = await session.get(ZAProject, project_id)
    if proj is None:
        raise HTTPException(status_code=404)
    await session.delete(proj)
    await session.commit()


def _out(p: ZAProject) -> dict:
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "source_type": p.source_type, "git_url": p.git_url, "git_branch": p.git_branch,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }
