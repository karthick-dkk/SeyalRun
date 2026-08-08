"""Template library API — list, get, and clone pre-built playbook templates."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog.templates import get_all_templates, get_template, get_template_categories
from ..database import get_async_session
from ..dependencies import get_current_user
from ..models.domain import Playbook
from ..models.schemas import (
    CloneTemplateRequest,
    PlaybookResponse,
    TemplateInfo,
    TemplateListResponse,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


def _template_to_info(t) -> TemplateInfo:
    import dataclasses
    from dataclasses import asdict

    from ..models.schemas import TemplateVarDef

    def _coerce_var(v) -> TemplateVarDef:
        if isinstance(v, TemplateVarDef):
            return v
        if dataclasses.is_dataclass(v):
            d = asdict(v)
        elif isinstance(v, dict):
            d = v
        else:
            d = {"name": str(v)}
        return TemplateVarDef(
            name=d.get("name", ""),
            description=d.get("description", ""),
            default_value=str(d.get("default", "")) if d.get("default") is not None else None,
            required=bool(d.get("required", False)),
        )

    return TemplateInfo(
        slug=t.slug,
        name=t.name,
        description=t.description,
        category=t.category,
        tags=t.tags,
        required_vars=[_coerce_var(v) for v in (t.required_vars or [])],
        tasks=t.tasks,
        variables=getattr(t, "variables", []),
        estimated_duration_seconds=t.estimated_duration_seconds,
        risk_level=t.risk_level,
    )


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    category: str | None = Query(None, description="Filter by category"),
) -> TemplateListResponse:
    """List all pre-built playbook templates."""
    templates = get_all_templates(category)
    items = [_template_to_info(t) for t in templates]
    return TemplateListResponse(total=len(items), items=items)


@router.get("/categories")
async def list_template_categories() -> list[str]:
    """Return distinct template categories."""
    return get_template_categories()


@router.get("/{slug}", response_model=TemplateInfo)
async def get_template_info(slug: str) -> TemplateInfo:
    """Get a single template by slug."""
    t = get_template(slug)
    if not t:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{slug}' not found",
        )
    return _template_to_info(t)


@router.post("/{slug}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_template(
    slug: str,
    body: dict,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> dict:
    """Clone a template and immediately execute it on the specified inventory."""
    t = get_template(slug)
    if not t:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Template '{slug}' not found"
        )

    inventory = body.get("inventory_selector", "all")
    extra_vars = body.get("extra_vars", {})

    import dataclasses

    from ..models.domain import Job
    from ..models.schemas import TaskDefinition, VariableDefinition
    from ..services.playbook_builder import build_yaml

    tasks = [TaskDefinition(**task) if isinstance(task, dict) else task for task in t.tasks]

    # Include required_vars defaults in YAML vars section so templates can reference them
    # User-supplied extra_vars override these defaults at runtime
    template_vars: list[VariableDefinition] = []
    for rv in t.required_vars or []:
        rv_dict = (
            dataclasses.asdict(rv)
            if dataclasses.is_dataclass(rv)
            else (rv if isinstance(rv, dict) else {})
        )
        default_val = rv_dict.get("default", "")
        var_name = rv_dict.get("name", "")
        if var_name:
            template_vars.append(
                VariableDefinition(
                    name=var_name,
                    default=default_val,
                    description=rv_dict.get("description", ""),
                    required=rv_dict.get("required", False),
                )
            )
    # Also add any explicit variables field
    for v in getattr(t, "variables", []):
        template_vars.append(VariableDefinition(**v) if isinstance(v, dict) else v)

    yaml_content = build_yaml(
        playbook_name=t.name, tasks=tasks, variables=template_vars, description=t.description
    )

    j = Job(
        status="pending",
        triggered_by=user["username"],
        inventory_selector=inventory,
        extra_vars=extra_vars,
        yaml_content=yaml_content,
    )
    db.add(j)
    await db.flush()
    await db.commit()

    import asyncio

    from ..services.execution import run_job_direct

    asyncio.create_task(run_job_direct(str(j.id)))

    log.info(
        "template_run_started",
        slug=slug,
        job_id=str(j.id),
        inventory=inventory,
        user=user["username"],
    )
    return {"job_id": str(j.id), "status": "pending", "template": slug}


@router.post("/{slug}/clone", response_model=PlaybookResponse, status_code=status.HTTP_201_CREATED)
async def clone_template(
    slug: str,
    body: CloneTemplateRequest,
    db: AsyncSession = Depends(get_async_session),
    user: dict = Depends(get_current_user),
) -> PlaybookResponse:
    """Clone a template into a new editable playbook."""
    t = get_template(slug)
    if not t:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{slug}' not found",
        )

    from ..models.schemas import TaskDefinition, VariableDefinition
    from ..services.playbook_builder import build_yaml

    name = body.name or f"{t.name} (copy)"
    tasks = [TaskDefinition(**task) if isinstance(task, dict) else task for task in t.tasks]
    variables = [
        VariableDefinition(**v) if isinstance(v, dict) else v for v in getattr(t, "variables", [])
    ]

    yaml_cache = build_yaml(
        playbook_name=name,
        tasks=tasks,
        variables=variables,
        description=t.description,
    )

    p = Playbook(
        name=name,
        description=body.description or t.description,
        category=t.category,
        tags=t.tags,
        tasks=[task.model_dump() for task in tasks],
        variables=[v.model_dump() for v in variables],
        yaml_cache=yaml_cache,
        is_template=False,
        source_template_id=None,
        created_by=user["username"],
    )
    db.add(p)
    await db.flush()

    log.info("template_cloned", slug=slug, playbook_id=str(p.id), user=user["username"])

    from .playbooks import _to_response

    return _to_response(p)
