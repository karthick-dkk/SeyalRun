"""Effective group POLICY resolver (v1.2) — the ONE place a user's group
memberships are reduced into policy decisions, mirroring rbacresolve.py's
role-resolution shape but for direct group settings rather than role grants.

Boolean policies (mfa_enforced, setup_wizard, single_session_enabled) are
OR-reduced across every group a user belongs to: if ANY group requires it,
the user is required to comply; there's no "most specific group wins"
precedence — enforcement is a floor, not an override.

IP restriction (v1.3) is a different shape — not a plain boolean, since each
active source (the user's own list, and/or each enabled group's own list)
contributes its OWN allowlist. "Most restrictive wins" there means every
independently-active constraint must match (AND across sources), not merely
whether any one of them is turned on — see ip_login_allowed.
"""

from __future__ import annotations

import ipaddress

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ZAGroupIpRestriction, ZAGroupNotifyConfig, ZAUser, ZAUserGroup, ZAUserGroupMember

_SEVERITY_RANK = {"info": 0, "medium": 1, "critical": 2}


async def effective_group_policies(session: AsyncSession, user_id: str) -> dict:
    docs = [
        doc for (doc,) in (
            await session.execute(
                select(ZAUserGroup.policies)
                .join(ZAUserGroupMember, ZAUserGroupMember.group_id == ZAUserGroup.id)
                .where(ZAUserGroupMember.user_id == user_id)
            )
        ).all()
    ]
    return {
        "mfa_enforced": any((d or {}).get("mfa_enforced") for d in docs),
        "setup_wizard": any((d or {}).get("setup_wizard") for d in docs),
        "single_session_enabled": any((d or {}).get("single_session_enabled") for d in docs),
    }


def _matches_any_cidr(addr: ipaddress.IPv4Address | ipaddress.IPv6Address, cidrs: list[str]) -> bool:
    for cidr in cidrs:
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue  # a malformed CIDR is skipped, not fatal — one bad entry can't lock every login out
    return False


async def ip_login_allowed(session: AsyncSession, user: ZAUser, client_ip: str) -> bool:
    """Most-restrictive-wins across every ACTIVE IP-restriction source: the
    user's own (if ip_restriction_enabled) and every group they belong to with
    policies.ip_restriction_enabled. Each active source must independently
    match the login IP — a user in an unrestricted group doesn't loosen their
    own restriction, and vice versa. No active source anywhere = unrestricted
    (today's default, unchanged)."""
    constraints: list[list[str]] = []
    if user.ip_restriction_enabled and user.allowed_ips:
        constraints.append(user.allowed_ips)

    rows = (
        await session.execute(
            select(ZAUserGroup.id, ZAUserGroup.policies)
            .join(ZAUserGroupMember, ZAUserGroupMember.group_id == ZAUserGroup.id)
            .where(ZAUserGroupMember.user_id == user.id)
        )
    ).all()
    active_group_ids = [gid for gid, policies in rows if (policies or {}).get("ip_restriction_enabled")]
    if active_group_ids:
        cfgs = (
            await session.execute(
                select(ZAGroupIpRestriction).where(ZAGroupIpRestriction.group_id.in_(active_group_ids))
            )
        ).scalars().all()
        for cfg in cfgs:
            if cfg.cidrs:
                constraints.append(cfg.cidrs)

    if not constraints:
        return True
    if not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return all(_matches_any_cidr(addr, cidr_list) for cidr_list in constraints)


async def notify_recipients(session: AsyncSession, user_id: str, severity: str) -> list[str]:
    """Every recipient email that should hear about a notification of this
    severity, across every group the user belongs to with notifications
    enabled and a min_severity at or below it. Deduped, order-independent."""
    rows = (
        await session.execute(
            select(ZAUserGroup.id, ZAUserGroup.policies)
            .join(ZAUserGroupMember, ZAUserGroupMember.group_id == ZAUserGroup.id)
            .where(ZAUserGroupMember.user_id == user_id)
        )
    ).all()
    eligible_group_ids = [gid for gid, policies in rows if (policies or {}).get("notifications_enabled")]
    if not eligible_group_ids:
        return []

    cfgs = (
        await session.execute(select(ZAGroupNotifyConfig).where(ZAGroupNotifyConfig.group_id.in_(eligible_group_ids)))
    ).scalars().all()
    sev_rank = _SEVERITY_RANK.get(severity, 0)
    emails: set[str] = set()
    for cfg in cfgs:
        if sev_rank >= _SEVERITY_RANK.get(cfg.min_severity, 1):
            emails.update(cfg.emails or [])
    return sorted(emails)
