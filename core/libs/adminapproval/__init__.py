"""SEYALRUN_ADMIN_APPROVAL: the one switch for "another admin must approve".

  off (the default)  an admin's own request does not wait for a second admin:
                     patch rollouts, package/reboot operations, job runs, schedules
                     and Zabbix triggers configured by admins, and access rules an
                     admin creates or edits. A non-admin's request is unchanged.
  on                 every approval step applies exactly as before (including
                     POSTURE_PATCH_APPROVAL=always|production for rollouts).
  anything else      treated as "on" (fail safe) and logged once as a warning.

Every service gets .env through env_file, so each one reads the raw value into its
own Settings and asks here what it means: the parsing lives in this one place.
Whatever skipped an approval because of the switch records NOT_REQUIRED in its
audit details, so the evidence never reads as if a second person had signed off.
"""

from __future__ import annotations

import logging

ENV_NAME = "SEYALRUN_ADMIN_APPROVAL"
DEFAULT = "off"
NOT_REQUIRED = "not required (SEYALRUN_ADMIN_APPROVAL=off)"
ADMIN_ROLES = frozenset({"superadmin", "admin"})

logger = logging.getLogger(__name__)
_warned: set[str] = set()


def approval_required(value: str | None) -> bool:
    """True when a second admin's approval still applies. ``None`` means unset (the
    default, off); an unrecognised value, including an empty one, means on."""
    raw = DEFAULT if value is None else str(value)
    v = raw.strip().lower()
    if v == "off":
        return False
    if v == "on":
        return True
    if v not in _warned:
        _warned.add(v)
        logger.warning("%s=%r is not 'on' or 'off'; treating it as 'on' (approvals required)", ENV_NAME, raw)
    return True


def is_admin_role(role: str | None) -> bool:
    return (role or "") in ADMIN_ROLES


def waiver(skipped: list[str] | tuple[str, ...]) -> dict:
    """What a run or record carries when the switch let it skip an approval, and
    what goes into its audit details. ``skipped`` names the gates that would have
    applied (e.g. "production host", "template requires approval")."""
    return {"approval": NOT_REQUIRED, "skipped": list(dict.fromkeys(skipped))}
