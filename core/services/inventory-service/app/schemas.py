from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ZoneCreate(BaseModel):
    name: str
    description: str = ""
    parent_zone_id: str | None = None


class ZoneOut(ZoneCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class GatewayCreate(BaseModel):
    zone_id: str | None = None
    name: str
    host: str
    port: int = 22
    username: str = ""
    credential_id: str | None = None


class GatewayOut(GatewayCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class HostGroupCreate(BaseModel):
    name: str
    description: str = ""


class HostGroupOut(HostGroupCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class HostCreate(BaseModel):
    name: str
    ip: str
    port: int = 22
    os_type: str = "linux"
    enabled: bool = True
    zone_id: str | None = None
    zabbix_hostid: str | None = None
    group_ids: list[str] = Field(default_factory=list)
    is_production: bool = False


class HostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    zabbix_hostid: str | None = None
    name: str
    ip: str
    port: int
    os_type: str
    enabled: bool
    zone_id: str | None = None
    last_synced_at: datetime | None = None
    is_reachable: bool | None = None
    last_ping_at: datetime | None = None
    created_at: datetime
    group_ids: list[str] = Field(default_factory=list)
    is_production: bool = False


class CredentialTemplateCreate(BaseModel):
    name: str
    secret_type: str = "password"  # password|ssh_key|vault_path
    description: str = ""
    default_username: str = ""
    default_params: dict = Field(default_factory=dict)
    push_enabled: bool = False
    rotation_days: int | None = None


class CredentialTemplateOut(CredentialTemplateCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class CredentialCreate(BaseModel):
    name: str = ""
    template_id: str | None = None
    username: str
    secret_type: str = "password"  # password|ssh_key|vault_path
    secret: dict = Field(default_factory=dict)  # empty dict on PUT = keep existing encrypted value
    credential_scope: str = "host"
    is_default: bool = False
    is_sudo: bool = False           # may use sudo for privileged (account) operations
    is_push_account: bool = False   # designated push account for its linked hosts
    host_ids: list[str] = Field(default_factory=list)


class CredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    template_id: str | None = None
    username: str
    secret_type: str
    credential_scope: str
    is_default: bool
    is_sudo: bool = False
    is_push_account: bool = False
    strength_score: int | None = None
    last_rotated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    host_ids: list[str] = Field(default_factory=list)


class RotationPolicyIn(BaseModel):
    rotation_days: int = 90
    rotation_mode: str = "auto"  # auto|manual
    enabled: bool = True


class RotationPolicyOut(RotationPolicyIn):
    model_config = ConfigDict(from_attributes=True)

    id: str
    credential_id: str
    last_rotated_at: datetime | None = None
    next_rotation_due: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CredentialHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    credential_id: str
    rotated_at: datetime
    rotated_by: str | None = None


class CredentialSecretOut(BaseModel):
    """Decrypted secret — returned only to authorized callers (e.g. terminal-service in Phase 2)."""

    id: str
    username: str
    secret_type: str
    secret: dict
    is_sudo: bool = False           # executors use sudo when the login isn't root
    is_push_account: bool = False   # executors gate account ops on sudo/push-account creds
