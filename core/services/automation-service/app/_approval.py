"""SEYALRUN_ADMIN_APPROVAL in automation-service: may this run skip its approval?

Only when the switch is off AND everyone who decided the run is an admin:

  * a person calling through the gateway (manual run, re-run): their real role,
    from X-User-Real-Role (never the write-vouched X-User-Role);
  * anything else (schedule, Zabbix trigger, posture-service on behalf of a
    person, a chain's parent run): the user ids behind it, looked up in
    identity-service. No ids, an unknown user, or an unreachable
    identity-service means "not shown to be admins", so the approval stays.

A run that skipped an approval this way carries WAIVER_PARAM (``_``-prefixed: no
caller can set it) and runner.py copies it into the job.start audit row.
"""

from __future__ import annotations

import logging

import httpx
from libs.adminapproval import approval_required, is_admin_role, waiver
from libs.servicetoken import mint

from ._attribution import accountable_ids, actor_user_id
from .config import get_settings

logger = logging.getLogger(__name__)

WAIVER_PARAM = "_approval_waived"
PRODUCTION = "production host"
TEMPLATE = "template requires approval"


def without_internal(params: dict | None) -> dict:
    """Stored or caller-supplied params (a template's default_params, a schedule's
    params_override) minus every ``_``-prefixed key. Those are set by this service
    only, and WAIVER_PARAM in particular is read back into the audit as "approval not
    required": a copy that arrived from stored data would be a forged one."""
    return {k: v for k, v in (params or {}).items() if not str(k).startswith("_")}


def admin_approval_required() -> bool:
    return approval_required(get_settings().seyalrun_admin_approval)


def caller_waiver(request, skipped: list[str]) -> dict | None:
    """The waiver for a person's own request, or None when approval still applies
    (or nothing would have needed it)."""
    if not skipped or admin_approval_required():
        return None
    headers = getattr(request, "headers", None) or {}
    if not is_admin_role(headers.get("X-User-Real-Role")):
        return None
    return waiver(skipped)


def deciders(triggered_by: str | None, accountable: list[str] | None) -> list[str]:
    """Who decided a run: the person for a user-started run, otherwise the person
    (if any) plus whoever configured the trigger."""
    actor = actor_user_id(triggered_by)
    if (triggered_by or "").startswith("user:") and actor:
        return [actor]
    return accountable_ids(actor, *(accountable or []))


async def fetch_roles(user_ids: list[str], transport: httpx.AsyncBaseTransport | None = None) -> dict[str, list[str] | None]:
    """{user id: the user's current effective roles, or None for an unknown or
    deactivated user, or a refused or ambiguous answer}. Raises httpx.HTTPError /
    ValueError when identity-service cannot be asked or answers nonsense."""
    settings = get_settings()
    headers = {"X-Service-Token": mint("automation-service", "identity-service", settings.service_jwt_secret)}
    out: dict[str, list[str] | None] = {}
    async with httpx.AsyncClient(base_url=settings.identity_service_url, timeout=10, transport=transport) as client:
        for uid in user_ids:
            r = await client.get(f"/api/v1/internal/users/{uid}/roles", headers=headers)
            body = r.json() if r.status_code == 200 else {}
            # Only an explicit known=True AND is_active=True counts: a deactivated
            # admin, or an identity-service too old to say, is not shown to be an admin.
            ok = isinstance(body, dict) and body.get("known") is True and body.get("is_active") is True
            out[uid] = list(body.get("roles") or []) if ok else None
    return out


async def users_are_admins(user_ids: list[str]) -> bool:
    ids = accountable_ids(*user_ids)
    if not ids:
        return False
    try:
        roles = await fetch_roles(ids)
    except (httpx.HTTPError, ValueError):
        logger.warning("identity-service could not confirm the roles; approval stays required")
        return False
    return all(any(is_admin_role(x) for x in (roles.get(uid) or [])) for uid in ids)


async def people_waiver(user_ids: list[str], skipped: list[str]) -> dict | None:
    """The waiver for a run decided by these people, or None when approval still applies."""
    if not skipped or admin_approval_required():
        return None
    if not await users_are_admins(user_ids):
        return None
    return waiver(skipped)
