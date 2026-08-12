"""Pydantic v2 request/response models — strict mode + extra=forbid on all."""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


# Playbook name: only alphanumeric, dashes, underscores, ending in .yml
_PLAYBOOK_RE = re.compile(r"^[a-zA-Z0-9_-]+\.yml$")

# Inventory selector: host IPs, hostnames, or group names
_INVENTORY_RE = re.compile(r"^[a-zA-Z0-9_,. /-]{1,500}$")


class AnsibleRunRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    playbook: Annotated[
        str,
        Field(
            pattern=r"^[a-zA-Z0-9_-]+\.yml$",
            max_length=100,
            description="Playbook filename (must be in approved list)",
        ),
    ]
    inventory_selector: Annotated[
        str,
        Field(
            max_length=500,
            description="Ansible inventory pattern (hosts, groups, IPs)",
        ),
    ]
    extra_vars: Annotated[
        dict[str, str],
        Field(
            default_factory=dict,
            description="Extra variables (max 20 keys, values max 200 chars each)",
        ),
    ]
    timeout_seconds: Annotated[
        int,
        Field(ge=30, le=3600, default=300),
    ]
    triggered_by: Annotated[
        str,
        Field(max_length=100, description="JumpServer username who triggered the job"),
    ]

    def validate_extra_vars(self) -> None:
        if len(self.extra_vars) > 20:
            raise ValueError("extra_vars: max 20 keys allowed")
        for k, v in self.extra_vars.items():
            if len(k) > 50 or len(v) > 200:
                raise ValueError(f"extra_vars: key/value too long for '{k}'")


class JobResponse(BaseModel):
    model_config = ConfigDict(strict=False, extra="ignore")

    job_id: UUID
    status: JobStatus
    playbook: str
    triggered_by: str
    message: str = ""


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(strict=False, extra="ignore")

    job_id: UUID
    status: JobStatus
    playbook: str
    triggered_by: str
    output_lines: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(strict=False, extra="ignore")

    error: str
    detail: Any = None
