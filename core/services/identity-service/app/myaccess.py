"""The caller's own effective access rules — pure selection, no DB/framework.

An access rule (za_authorizations) reaches a user two ways: it names the user
directly (user_id / user_ids) or it names a user group the user belongs to
(user_group_id / user_group_ids). The internal resolvers (api/internal.py
authz/host-ids, authz/resolve) already honour both; this is the same principal
match, shaped for display — one row per rule, saying how it applies and, for a
group grant, through which of the caller's groups.

Kept stdlib-only so the test suite exercises it directly. The HTTP wrapper is
api/my_access.py, which passes the caller's id from the gateway-set X-User-Id and
nothing else — there is no parameter by which one user could ask for another's.
"""

from __future__ import annotations

from datetime import datetime

# Statuses worth showing the user: in force, or waiting on an approver.
_SHOWN = ("active", "pending_approval")


def _ids(arr, scalar) -> list[str]:
    out = list(arr or [])
    if scalar and scalar not in out:
        out.append(scalar)
    return out


def effective_access_rows(authzs, user_id: str, groups: dict[str, str], now: datetime) -> list[dict]:
    """Rules that apply to ``user_id`` (direct or via one of ``groups`` =
    {group_id: group_name} the user is a member of), excluding rejected,
    disabled and already-expired rules.

    Each row carries only what describes the caller's own access: rule name,
    actions, target host/group ids, validity window, status, and how it applies
    (``via`` = "direct" | "group", ``groups`` = names of the caller's groups that
    grant it). Other principals on the same rule (other user ids, groups the
    caller is not in) and credential ids are never copied out."""
    rows: list[dict] = []
    for a in authzs:
        status = getattr(a, "status", None) or ("active" if getattr(a, "enabled", False) else "")
        if status not in _SHOWN:
            continue
        if status == "active" and not getattr(a, "enabled", True):
            continue
        expired = getattr(a, "date_expired", None)
        if expired is not None and expired < now:
            continue
        direct = user_id in _ids(getattr(a, "user_ids", None), getattr(a, "user_id", None))
        via_groups = [gid for gid in _ids(getattr(a, "user_group_ids", None), getattr(a, "user_group_id", None))
                      if gid in groups]
        if not direct and not via_groups:
            continue
        start = getattr(a, "date_start", None)
        rows.append({
            "id": a.id,
            "name": a.name,
            "actions": list(getattr(a, "actions", None) or []),
            "host_ids": _ids(getattr(a, "host_ids", None), getattr(a, "host_id", None)),
            "host_group_ids": _ids(getattr(a, "host_group_ids", None), getattr(a, "host_group_id", None)),
            "date_start": start.isoformat() if start else None,
            "date_expired": expired.isoformat() if expired else None,
            "status": status,
            "via": "direct" if direct else "group",
            "groups": sorted(groups[g] for g in via_groups),
        })
    rows.sort(key=lambda r: (r["via"] != "direct", r["name"].lower()))
    return rows
