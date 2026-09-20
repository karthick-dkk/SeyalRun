"""Integration registry state: which discovered plugins are switched off, and the
connection config for the integrations that have one.

Plugins are NOT installed here. They ship inside the image and are discovered at
startup (libs/pluginbase) — this endpoint only records an admin's decision to
stop using one, which is a configuration change rather than a code change. That
distinction is the whole security posture: nothing in this file can add code to
a running deployment.

Two axes have no off switch at all (see libs.pluginbase.registry.AXES): turning
off the key provider or a credential kind is not a configuration change but an
outage, so ``disable`` refuses them rather than offering a switch that bricks the
vault.

Secrets (salt eauth password) follow the same rule as the Zabbix API token in
settings.py: stored, never returned, and a blank value on write means "keep the
existing one" so a UI round-trip cannot silently erase it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from libs.pluginbase import AXES

from ..audit import log_action
from ..database import get_session
from ..deps import require_admin, require_service_token, require_superadmin
from ..models import ZASetting

router = APIRouter(prefix="/integrations", tags=["integrations"], dependencies=[Depends(require_service_token)])
internal_router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(require_service_token)])

STATE_KEY = "integrations"

# config blocks this API accepts, and which of their fields are secret
_CONFIG_KINDS: dict[str, tuple[str, ...]] = {
    "salt": ("eauth_password",),
    "galaxy": ("api_key",),
}


def toggle_refusal(axis: str) -> tuple[int, str] | None:
    """(status, detail) if this axis may not be toggled, else None.

    Pure, so the rule that protects the vault is testable without a database or a
    running service — see core/tests/test_integration_registry.py.
    """
    meta = AXES.get(axis)
    if meta is None:
        return (status.HTTP_404_NOT_FOUND, f"unknown plugin axis: {axis}")
    _label, togglable, locked_reason = meta
    if not togglable:
        return (status.HTTP_409_CONFLICT, locked_reason)
    return None


def apply_toggle(disabled: dict, axis: str, name: str, enabled: bool) -> dict:
    """The new disabled-map after this toggle. Pure and idempotent: enabling twice
    leaves one absence, disabling twice leaves one entry, not two."""
    out = {k: list(v) for k, v in (disabled or {}).items()}
    current = list(out.get(axis, []))
    if enabled:
        current = [n for n in current if n != name]
    elif name not in current:
        current.append(name)
    out[axis] = sorted(current)
    return out


class PluginToggle(BaseModel):
    enabled: bool


class ConfigIn(BaseModel):
    values: dict = {}


async def get_integration_state(session: AsyncSession) -> dict:
    """Public reader for other modules in this service (auth.py's IdP gate)."""
    return await _load(session)


async def _load(session: AsyncSession) -> dict:
    row = await session.get(ZASetting, STATE_KEY)
    value = dict(row.value) if row and isinstance(row.value, dict) else {}
    value.setdefault("disabled", {})
    value.setdefault("config", {})
    return value


async def _save(session: AsyncSession, value: dict) -> None:
    row = await session.get(ZASetting, STATE_KEY)
    if row is None:
        session.add(ZASetting(key=STATE_KEY, value=value))
    else:
        # Reassign rather than mutate: SQLAlchemy does not see in-place edits of a
        # JSON column, so an in-place update would commit nothing.
        row.value = dict(value)
    await session.commit()


def _redact(config: dict) -> dict:
    out = {}
    for kind, values in (config or {}).items():
        secrets = _CONFIG_KINDS.get(kind, ())
        clean = {k: v for k, v in (values or {}).items() if k not in secrets}
        for s in secrets:
            clean[f"{s}_configured"] = bool((values or {}).get(s))
        out[kind] = clean
    return out


@router.get("/state", dependencies=[Depends(require_admin)])
async def get_state(session: AsyncSession = Depends(get_session)):
    """Admin view: what's switched off, and the (redacted) integration config."""
    value = await _load(session)
    return {"disabled": value["disabled"], "config": _redact(value["config"])}


@router.put("/plugins/{axis}/{name}", dependencies=[Depends(require_superadmin)])
async def toggle_plugin(
    axis: str,
    name: str,
    payload: PluginToggle,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    refusal = toggle_refusal(axis)
    if refusal is not None:
        raise HTTPException(status_code=refusal[0], detail=refusal[1])

    value = await _load(session)
    value["disabled"] = apply_toggle(value["disabled"], axis, name, payload.enabled)
    await _save(session, value)

    await log_action(
        session, user_id=actor_id, username=actor_name or "",
        action="integration.plugin_enabled" if payload.enabled else "integration.plugin_disabled",
        resource_type="plugin", resource_id=f"{axis}/{name}",
        details={"axis": axis, "plugin": name, "enabled": payload.enabled},
        result="success",
    )
    return {"disabled": value["disabled"]}


@router.put("/config/{kind}", dependencies=[Depends(require_superadmin)])
async def put_config(
    kind: str,
    payload: ConfigIn,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    if kind not in _CONFIG_KINDS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown integration: {kind}")
    value = await _load(session)
    config = dict(value["config"])
    current = dict(config.get(kind) or {})
    incoming = {k: (v.strip() if isinstance(v, str) else v) for k, v in (payload.values or {}).items()}
    for secret in _CONFIG_KINDS[kind]:
        # blank = keep: a form that never renders the secret must not be able to
        # clear it just by being submitted
        if not incoming.get(secret):
            incoming.pop(secret, None)
            if current.get(secret):
                incoming[secret] = current[secret]
    config[kind] = incoming
    value["config"] = config
    await _save(session, value)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="integration.config_update",
        resource_type="integration", resource_id=kind,
        details={"integration": kind, "fields": sorted(k for k in incoming if k not in _CONFIG_KINDS[kind])},
        result="success",
    )
    return _redact(config)[kind]


@internal_router.get("/integration-state")
async def internal_integration_state(session: AsyncSession = Depends(get_session)):
    """Service-to-service view, secrets included — automation-service needs the salt
    credentials to run a job, and every service needs the disabled map to honour it.
    Never reachable from the browser: api-gateway does not route /internal.
    """
    value = await _load(session)
    return {"disabled": value["disabled"], "config": value["config"]}
