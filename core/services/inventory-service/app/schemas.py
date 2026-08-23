from __future__ import annotations

from datetime import datetime

from typing import Literal

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


# Constrained rather than free strings. An unvalidated host_type let
# {"host_type": "totally-made-up"} be stored: it is then neither a server nor a
# gateway, so it silently drops out of gateway resolution while still looking
# like a configured jump point in the UI. Anything that later branches on this
# field inherits that ambiguity, and the UI cannot be the gate — the API is
# reachable directly.
HostType = Literal["server", "gateway"]
OsType = Literal["linux", "windows"]


class HostCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    ip: str = Field(min_length=1, max_length=100)
    port: int = Field(default=22, ge=1, le=65535)
    os_type: OsType = "linux"
    # A gateway is an ordinary host used as a jump point, so it carries groups,
    # a zone and its own credential like any other.
    host_type: HostType = "server"
    # Position among its zone's gateways; ignored for host_type "server".
    # Bounded: the value only orders hops, and a negative or absurd one is a typo
    # that would silently reshuffle a connection chain.
    gateway_order: int = Field(default=0, ge=0, le=99)
    enabled: bool = True
    zone_id: str | None = None
    zabbix_hostid: str | None = None
    # Required, enforced in the endpoint rather than with min_length here so the
    # caller gets an explanation instead of a schema error naming a field.
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
    host_type: str = "server"
    gateway_order: int = 0
    enabled: bool
    zone_id: str | None = None
    last_synced_at: datetime | None = None
    is_reachable: bool | None = None
    last_ping_at: datetime | None = None
    created_at: datetime
    group_ids: list[str] = Field(default_factory=list)
    is_production: bool = False


class CredentialTemplateBase(BaseModel):
    name: str
    secret_type: str = "password"  # password|ssh_key|vault_path
    description: str = ""
    default_username: str = ""
    default_params: dict = Field(default_factory=dict)
    push_enabled: bool = False
    rotation_days: int | None = None


class CredentialTemplateCreate(CredentialTemplateBase):
    # Write-only, and deliberately not on the Base: CredentialTemplateOut used to
    # inherit CredentialTemplateCreate, so a `secret` field added there would have
    # been returned by the plain list endpoint — every stored template secret handed
    # to any admin, with no reveal token, no elevation and no audit row. Splitting a
    # Base out is what makes that mistake unrepresentable rather than merely avoided.
    secret: dict = Field(default_factory=dict)  # {} on PUT = keep the existing secret


class CredentialTemplateOut(CredentialTemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    # Whether a secret is stored — never the secret itself. Lets the UI show
    # "secret set" and offer Reveal without another round trip.
    has_secret: bool = False


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
