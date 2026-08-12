"""Pydantic request/response schemas — strict mode + extra=forbid."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Module catalog schemas ───────────────────────────────────────────────────


class ParamSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: Literal["str", "int", "bool", "list", "dict", "path"]
    required: bool = False
    default: Any = None
    description: str = ""
    choices: list[str] = Field(default_factory=list)
    example: Any = None


class ModuleInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    short_name: str
    category: str
    description: str
    docs_url: str
    params: list[ParamSchema]
    example_task: dict = Field(default_factory=dict)


class ModuleListResponse(BaseModel):
    total: int
    items: list[ModuleInfo]


# ── Playbook schemas ─────────────────────────────────────────────────────────


class TaskDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")  # ignore unknown fields gracefully

    task_id: Annotated[str, Field(max_length=50)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    module: Annotated[str, Field(max_length=200)]
    params: Annotated[dict[str, Any], Field(default_factory=dict)]
    when: str | None = None
    register: str | None = None
    become: bool = False
    ignore_errors: bool = False
    notify: list[str] | None = None
    tags: list[str] | None = None
    loop: Any = None  # Ansible loop over a list
    loop_control: dict | None = None  # Control loop variables (loop_var, label, etc.)
    delegate_to: str | None = None  # Run task on a different host
    no_log: bool | None = None  # Hide sensitive output


class VariableDefinition(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: Annotated[str, Field(pattern=r"^[a-zA-Z_]\w*$", max_length=50)]
    default: Any = None
    description: str = ""
    required: bool = False


class PlaybookCreate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[\w\s_\-]+$")]
    description: str = ""
    category: Annotated[str, Field(default="system", max_length=50)]
    tags: list[str] = Field(default_factory=list)
    tasks: list[TaskDefinition] = Field(default_factory=list)
    variables: list[VariableDefinition] = Field(default_factory=list)


class PlaybookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str | None, Field(max_length=200)] = None
    description: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    tasks: list[TaskDefinition] | None = None
    variables: list[VariableDefinition] | None = None


class PlaybookResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    name: str
    description: str
    category: str
    tags: list[str]
    tasks: list[dict]
    variables: list[dict]
    is_template: bool
    source_template_id: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class PlaybookListResponse(BaseModel):
    total: int
    items: list[PlaybookResponse]


# ── Template schemas ─────────────────────────────────────────────────────────


class TemplateVarDef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: str = "str"
    description: str = ""
    required: bool = False
    default: Any = None


class TemplateInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str
    name: str
    description: str
    category: str
    tags: list[str]
    required_vars: list[TemplateVarDef]
    tasks: list[dict]
    estimated_duration_seconds: int
    risk_level: Literal["low", "medium", "high"]


class TemplateListResponse(BaseModel):
    total: int
    items: list[TemplateInfo]


class CloneTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    description: str | None = None


# ── Job schemas ──────────────────────────────────────────────────────────────


class JobCreate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    playbook_id: uuid.UUID | None = None
    yaml_content: Annotated[str | None, Field(max_length=100_000)] = None
    inventory_selector: Annotated[str, Field(default="all", max_length=500)]
    extra_vars: Annotated[dict[str, Any], Field(default_factory=dict)]
    triggered_by: Annotated[str, Field(max_length=100)] = "api"


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    playbook_id: uuid.UUID | None
    status: str
    triggered_by: str
    ab_job_id: str | None
    inventory_selector: str
    extra_vars: dict
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None
    exit_code: int | None
    output_lines: list[str]
    created_at: datetime


class JobListResponse(BaseModel):
    total: int
    items: list[JobResponse]


# ── Alert schemas ────────────────────────────────────────────────────────────


class AlertConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playbook_pattern: str = "*"
    triggered_by_pattern: str = "*"
    duration_threshold_seconds: int = 300
    host_group_pattern: str = "*"
    severity_min: Annotated[int, Field(ge=1, le=5)] = 3


class AlertChannel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["webhook", "email", "slack", "teams"]
    url: str | None = None
    to: list[str] = Field(default_factory=list)
    name: str | None = None
    secret: str | None = None


class AlertRuleCreate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: str = ""
    enabled: bool = True
    event_type: Literal[
        "job_failed", "job_slow", "connectivity_lost", "ldap_sync_failed", "zabbix_webhook"
    ]
    conditions: AlertConditions = Field(default_factory=AlertConditions)
    channels: Annotated[list[AlertChannel], Field(min_length=1)]


class AlertRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    conditions: AlertConditions | None = None
    channels: list[AlertChannel] | None = None


class AlertRuleResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    name: str
    description: str
    enabled: bool
    event_type: str
    conditions: dict
    channels: list[dict]
    created_by: str
    created_at: datetime
    updated_at: datetime


class AlertRuleListResponse(BaseModel):
    total: int
    items: list[AlertRuleResponse]


class AlertHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    rule_id: uuid.UUID | None
    rule_name: str
    event_type: str
    event_payload: dict
    channels_tried: list
    delivery_status: str
    delivered_at: datetime
    error_detail: str | None


class AlertHistoryListResponse(BaseModel):
    total: int
    items: list[AlertHistoryResponse]


class ChannelCreate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    channel_type: Literal["webhook", "email", "slack", "teams"]
    config: dict = Field(default_factory=dict)


class ChannelResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    name: str
    channel_type: str
    config: dict
    is_active: bool
    created_at: datetime
