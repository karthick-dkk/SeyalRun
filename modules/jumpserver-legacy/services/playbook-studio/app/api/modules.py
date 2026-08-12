"""Module catalog API — serves the 30 static Ansible module definitions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from ..catalog.modules import get_all_modules, get_categories, get_module
from ..models.schemas import ModuleInfo, ModuleListResponse

router = APIRouter(prefix="/api/v1/modules", tags=["modules"])


@router.get("", response_model=ModuleListResponse)
async def list_modules(
    category: str | None = Query(None, description="Filter by category"),
) -> ModuleListResponse:
    """List all available Ansible modules (optionally filtered by category)."""
    modules = get_all_modules(category)
    return ModuleListResponse(total=len(modules), items=modules)


@router.get("/categories")
async def list_categories() -> list[str]:
    """Return distinct module categories."""
    return get_categories()


@router.get("/{module_name}/params")
async def get_module_params(module_name: str) -> dict:
    """Get only the parameter schema for a module (lightweight endpoint for form building)."""
    full_name = module_name.replace("__", ".")
    module = get_module(full_name) or get_module(module_name)
    if not module:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Module '{module_name}' not found",
        )
    return {
        "module": module.name,
        "params": [p.model_dump() for p in module.params],
        "example_task": module.example_task,
    }


@router.get("/{module_name:path}", response_model=ModuleInfo)
async def get_module_info(module_name: str) -> ModuleInfo:
    """Get full module info including parameter schema (drives UI form generation)."""
    module = get_module(module_name)
    if not module:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Module '{module_name}' not found in catalog",
        )
    return module
