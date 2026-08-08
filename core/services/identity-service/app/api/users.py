from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.servicetoken import mint

from ..audit import log_action
from ..config import get_settings
from ..database import get_session
from ..deps import require_admin, require_service_token, require_superadmin
from ..models import ZAGroupIpRestriction, ZAGroupNotifyConfig, ZAGroupRole, ZARole, ZAUser, ZAUserGroup, ZAUserGroupMember, ZAUserRole
from ..schemas import (
    GroupIpRestrictionUpdate,
    GroupMembersUpdate,
    GroupNotifyConfigUpdate,
    GroupPoliciesUpdate,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    GroupRolesUpdate,
    UserCreate,
    UserGroupCreate,
    UserGroupOut,
    UserOut,
    UserUpdate,
)
from ..security import hash_password

router = APIRouter(tags=["users"], dependencies=[Depends(require_service_token)])


# ── Superadmin binding gate + last-superadmin lockout guard (v2 security) ─────
async def _superadmin_role_id(session: AsyncSession) -> str | None:
    r = await session.execute(select(ZARole.id).where(ZARole.name == "superadmin"))
    return r.scalar_one_or_none()


def _guard_superadmin_bind(role_ids: list[str] | None, superadmin_id: str | None, actor_role: str) -> None:
    """Attaching the superadmin role (to a user OR a group) requires the actor to be
    superadmin — closes the self-escalation path where any users/roles/groups-writer
    could grant themselves superadmin."""
    if superadmin_id and role_ids and superadmin_id in role_ids and actor_role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="only a superadmin may assign the superadmin role")


async def _effective_superadmin_count(session: AsyncSession) -> int:
    """Distinct ACTIVE users holding superadmin via direct role OR group grant."""
    sid = await _superadmin_role_id(session)
    if not sid:
        return 0
    users: set[str] = {u for (u,) in (await session.execute(
        select(ZAUserRole.user_id).where(ZAUserRole.role_id == sid))).all()}
    users |= {u for (u,) in (await session.execute(
        select(ZAUserGroupMember.user_id)
        .join(ZAGroupRole, ZAGroupRole.group_id == ZAUserGroupMember.group_id)
        .where(ZAGroupRole.role_id == sid))).all()}
    if not users:
        return 0
    active = await session.execute(
        select(ZAUser.id).where(ZAUser.id.in_(users), ZAUser.is_active.is_(True)))
    return len({u for (u,) in active.all()})


async def _guard_last_superadmin(session: AsyncSession) -> None:
    """Call AFTER applying a change, BEFORE commit: block if it zeroed effective
    superadmins so the caller rolls back."""
    if await _effective_superadmin_count(session) == 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="operation would remove the last superadmin")


async def _target_is_superadmin(session: AsyncSession, user_id: str) -> bool:
    from ..rbacresolve import effective_roles
    return "superadmin" in await effective_roles(session, user_id)


def _guard_superadmin_target(is_super_target: bool, actor_role: str) -> None:
    """Mutating a user who effectively holds superadmin (password reset, deactivate,
    role change) requires the actor to be superadmin — else a plain admin could reset a
    superadmin's password and take over the account."""
    if is_super_target and actor_role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="only a superadmin may modify a superadmin account")


def _guard_grants_allpower(permissions: dict | None, actor_role: str) -> None:
    """Creating/updating a role that grants all-access or the reveal flag requires
    superadmin — else an admin could mint an {all:true} role and self-assign it,
    escaping the superadmin boundary the role-binding gate protects."""
    p = permissions or {}
    if (p.get("all") or "reveal" in (p.get("flags") or [])) and actor_role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="only a superadmin may create/grant an all-access or reveal-capable role")


def _guard_group_mfa_enforce(current: bool, new: bool, actor_role: str) -> None:
    """Changing group-level MFA enforcement in EITHER direction requires superadmin.
    Turning it ON forces every member into MFA (same sensitivity tier as reveal/
    all-access role grants, and it bypasses members' own role-level "mfa" flag).
    Turning it OFF removes a security control from every member — asymmetric
    gating (superadmin-only ON, unrestricted OFF) would let any plain admin
    silently strip a superadmin-imposed MFA requirement from a target group."""
    if current != new and actor_role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="only a superadmin may change MFA enforcement for a group")


@router.get("/users/groups/{group_id}/roles")
async def get_group_roles(group_id: str, session: AsyncSession = Depends(get_session)):
    rows = await session.execute(select(ZAGroupRole.role_id).where(ZAGroupRole.group_id == group_id))
    return {"group_id": group_id, "role_ids": [r for (r,) in rows.all()]}


@router.put("/users/groups/{group_id}/roles", dependencies=[Depends(require_admin)])
async def set_group_roles(
    group_id: str,
    payload: GroupRolesUpdate,
    session: AsyncSession = Depends(get_session),
    actor_role: str = Header(default="user", alias="X-User-Role"),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    """Bind roles to a group — members inherit them. Superadmin-gated for the
    superadmin role; guarded against zeroing the last superadmin."""
    if (await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    sid = await _superadmin_role_id(session)
    _guard_superadmin_bind(payload.role_ids, sid, actor_role)

    existing = await session.execute(select(ZAGroupRole).where(ZAGroupRole.group_id == group_id))
    for link in existing.scalars().all():
        await session.delete(link)
    await session.flush()
    for rid in dict.fromkeys(payload.role_ids):
        session.add(ZAGroupRole(group_id=group_id, role_id=rid))
    await session.flush()
    await _guard_last_superadmin(session)  # raises 409 → transaction not committed
    await session.commit()

    await log_action(session, user_id=actor_id, username=actor_name or "", action="group.roles.set",
                     resource_type="user-group", resource_id=group_id, details={"role_ids": payload.role_ids})
    return {"group_id": group_id, "role_ids": payload.role_ids}


@router.put("/users/groups/{group_id}/policies", response_model=UserGroupOut, dependencies=[Depends(require_admin)])
async def set_group_policies(
    group_id: str,
    payload: GroupPoliciesUpdate,
    session: AsyncSession = Depends(get_session),
    actor_role: str = Header(default="user", alias="X-User-Role"),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    """Changing mfa_enforced (either direction) requires a genuine superadmin (see
    _guard_group_mfa_enforce); setup_wizard/notifications_enabled/single_session_enabled/
    ip_restriction_enabled stay admin-gated like the rest of group management."""
    result = await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")

    current_mfa_enforced = bool((group.policies or {}).get("mfa_enforced"))
    _guard_group_mfa_enforce(current_mfa_enforced, payload.mfa_enforced, actor_role)

    group.policies = {
        "mfa_enforced": payload.mfa_enforced,
        "setup_wizard": payload.setup_wizard,
        "notifications_enabled": payload.notifications_enabled,
        "single_session_enabled": payload.single_session_enabled,
        "ip_restriction_enabled": payload.ip_restriction_enabled,
    }
    await session.commit()
    await session.refresh(group)

    await log_action(session, user_id=actor_id, username=actor_name or "", action="group.policies.set",
                     resource_type="user-group", resource_id=group_id, details=group.policies)
    return group


async def _get_or_create_ip_restriction(session: AsyncSession, group_id: str) -> ZAGroupIpRestriction:
    result = await session.execute(select(ZAGroupIpRestriction).where(ZAGroupIpRestriction.group_id == group_id))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = ZAGroupIpRestriction(group_id=group_id, cidrs=[])
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
    return cfg


@router.get("/users/groups/{group_id}/ip-restriction", dependencies=[Depends(require_admin)])
async def get_group_ip_restriction(group_id: str, session: AsyncSession = Depends(get_session)):
    if (await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    cfg = await _get_or_create_ip_restriction(session, group_id)
    return {"cidrs": cfg.cidrs}


@router.put("/users/groups/{group_id}/ip-restriction", dependencies=[Depends(require_admin)])
async def put_group_ip_restriction(
    group_id: str,
    payload: GroupIpRestrictionUpdate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    if (await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    cfg = await _get_or_create_ip_restriction(session, group_id)
    cfg.cidrs = payload.cidrs
    await session.commit()
    await log_action(session, user_id=actor_id, username=actor_name or "", action="group.ip_restriction.set",
                     resource_type="user-group", resource_id=group_id, details={"cidrs": payload.cidrs})
    return {"cidrs": cfg.cidrs}


async def _get_or_create_notify_config(session: AsyncSession, group_id: str) -> ZAGroupNotifyConfig:
    result = await session.execute(select(ZAGroupNotifyConfig).where(ZAGroupNotifyConfig.group_id == group_id))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = ZAGroupNotifyConfig(group_id=group_id, emails=[], min_severity="medium")
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
    return cfg


@router.get("/users/groups/{group_id}/notify-config", dependencies=[Depends(require_admin)])
async def get_group_notify_config(group_id: str, session: AsyncSession = Depends(get_session)):
    if (await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    cfg = await _get_or_create_notify_config(session, group_id)
    return {"emails": cfg.emails, "min_severity": cfg.min_severity}


@router.put("/users/groups/{group_id}/notify-config", dependencies=[Depends(require_admin)])
async def put_group_notify_config(
    group_id: str,
    payload: GroupNotifyConfigUpdate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    if (await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    cfg = await _get_or_create_notify_config(session, group_id)
    cfg.emails = payload.emails
    cfg.min_severity = payload.min_severity
    await session.commit()
    await log_action(session, user_id=actor_id, username=actor_name or "", action="group.notify_config.set",
                     resource_type="user-group", resource_id=group_id,
                     details={"emails": payload.emails, "min_severity": payload.min_severity})
    return {"emails": cfg.emails, "min_severity": cfg.min_severity}


async def _user_roles(session: AsyncSession, user_id: str) -> tuple[list[str], list[str]]:
    rows = await session.execute(
        select(ZARole.id, ZARole.name).join(ZAUserRole, ZARole.id == ZAUserRole.role_id).where(ZAUserRole.user_id == user_id)
    )
    pairs = rows.all()
    return [n for (_i, n) in pairs], [i for (i, _n) in pairs]


async def _set_user_roles(session: AsyncSession, user_id: str, role_ids: list[str]) -> None:
    existing = await session.execute(select(ZAUserRole).where(ZAUserRole.user_id == user_id))
    for link in existing.scalars().all():
        await session.delete(link)
    for rid in dict.fromkeys(role_ids):  # de-dup, preserve order
        session.add(ZAUserRole(user_id=user_id, role_id=rid))


async def _user_out(session: AsyncSession, user: ZAUser) -> UserOut:
    from ..rbacresolve import effective_roles
    # role_ids stay DIRECT assignments (what the edit UI toggles); the `roles` display
    # shows EFFECTIVE names (direct ∪ group-inherited) so the Users pane is truthful.
    _direct_names, role_ids = await _user_roles(session, user.id)
    role_names = await effective_roles(session, user.id)
    role_name = role_names[0] if role_names else None
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        zabbix_userid=user.zabbix_userid,
        role_id=user.role_id,
        role_name=role_name,
        roles=role_names,
        role_ids=role_ids,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        totp_enabled=user.totp_enabled,
        mfa_method=user.mfa_method,
        allowed_ips=user.allowed_ips or [],
        ip_restriction_enabled=user.ip_restriction_enabled,
        single_session_enabled=user.single_session_enabled,
        is_service_account=user.is_service_account,
        account_type=user.account_type,
        created_at=user.created_at,
    )


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZARole))
    return result.scalars().all()


_RESERVED_ROLE_NAMES = {"superadmin", "admin", "automation", "support", "audit", "guest", "user"}


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_role(
    payload: RoleCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    actor_role: str = Header(default="user", alias="X-User-Role"),
):
    _guard_grants_allpower(payload.permissions, actor_role)
    name = payload.name.strip().lower()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="role name required")
    if name in _RESERVED_ROLE_NAMES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="that name is reserved for a built-in role")
    existing = await session.execute(select(ZARole).where(ZARole.name == name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="role name already exists")
    role = ZARole(name=name, description=payload.description, permissions=payload.permissions or {}, is_builtin=False)
    session.add(role)
    await session.commit()
    await session.refresh(role)
    await log_action(session, user_id=actor_id, username=actor_name or "", action="role.create",
                     resource_type="role", resource_id=role.id, details={"name": role.name})
    return role


@router.put("/roles/{role_id}", response_model=RoleOut, dependencies=[Depends(require_admin)])
async def update_role(
    role_id: str,
    payload: RoleUpdate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    actor_role: str = Header(default="user", alias="X-User-Role"),
):
    role = await session.get(ZARole, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="role not found")
    if role.is_builtin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="built-in roles cannot be edited")
    if payload.description is not None:
        role.description = payload.description
    if payload.permissions is not None:
        _guard_grants_allpower(payload.permissions, actor_role)
        role.permissions = payload.permissions
    await session.commit()
    await session.refresh(role)
    await log_action(session, user_id=actor_id, username=actor_name or "", action="role.update",
                     resource_type="role", resource_id=role.id, details={"name": role.name})
    return role


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_role(
    role_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    role = await session.get(ZARole, role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="role not found")
    if role.is_builtin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="built-in roles cannot be deleted")
    await session.delete(role)  # za_user_roles rows cascade
    await session.commit()
    await log_action(session, user_id=actor_id, username=actor_name or "", action="role.delete",
                     resource_type="role", resource_id=role_id, details={"name": role.name})


@router.get("/users", response_model=list[UserOut])
async def list_users(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAUser))
    return [await _user_out(session, u) for u in result.scalars().all()]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_user(
    payload: UserCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    actor_role: str = Header(default="user", alias="X-User-Role"),
):
    existing = await session.execute(select(ZAUser).where(ZAUser.username == payload.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username already exists")

    role_ids = payload.role_ids or ([payload.role_id] if payload.role_id else [])
    _guard_superadmin_bind(role_ids, await _superadmin_role_id(session), actor_role)
    user = ZAUser(
        username=payload.username,
        display_name=payload.display_name,
        email=payload.email,
        role_id=payload.role_id or (role_ids[0] if role_ids else None),
        password_hash=hash_password(payload.password),
        is_service_account=payload.is_service_account,
        account_type="service" if payload.is_service_account else "human",
    )
    session.add(user)
    await session.flush()
    if role_ids:
        await _set_user_roles(session, user.id, role_ids)
    await session.commit()
    await session.refresh(user)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="user.create",
        resource_type="user", resource_id=user.id, details={"username": user.username},
    )
    return await _user_out(session, user)


@router.put("/users/{user_id}", response_model=UserOut, dependencies=[Depends(require_admin)])
async def update_user(
    user_id: str,
    payload: UserUpdate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    actor_role: str = Header(default="user", alias="X-User-Role"),
):
    result = await session.execute(select(ZAUser).where(ZAUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    # Protect a superadmin target from takeover by a plain admin (password/deactivate/role).
    if payload.password or payload.is_active is not None or payload.role_ids is not None or payload.role_id is not None:
        _guard_superadmin_target(await _target_is_superadmin(session, user_id), actor_role)

    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.email is not None:
        user.email = payload.email
    if payload.role_id is not None:
        user.role_id = payload.role_id
    if payload.role_ids is not None:
        _guard_superadmin_bind(payload.role_ids, await _superadmin_role_id(session), actor_role)
        await _set_user_roles(session, user.id, payload.role_ids)
        # keep legacy role_id pointing at the first assigned role
        user.role_id = payload.role_ids[0] if payload.role_ids else None
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)
    if payload.allowed_ips is not None:
        user.allowed_ips = payload.allowed_ips
    if payload.ip_restriction_enabled is not None:
        user.ip_restriction_enabled = payload.ip_restriction_enabled
    if payload.single_session_enabled is not None:
        user.single_session_enabled = payload.single_session_enabled
    if payload.is_service_account is not None:
        user.is_service_account = payload.is_service_account
        user.account_type = "service" if payload.is_service_account else "human"

    # Guard before commit: a role change or deactivation must not zero the superadmins.
    await session.flush()
    await _guard_last_superadmin(session)
    await session.commit()
    await session.refresh(user)

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="user.update",
        resource_type="user", resource_id=user.id,
    )
    return await _user_out(session, user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_superadmin)])
async def delete_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZAUser).where(ZAUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if user.zabbix_userid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Zabbix-synced users cannot be deleted from SeyalRun — remove the user in Zabbix and re-sync instead",
        )
    if user_id == actor_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot delete your own account")

    username = user.username

    # ZAUserRole and ZAUserGroupMember are association *objects* with composite
    # primary keys (user_id is part of the PK, not a plain FK column), so
    # SQLAlchemy's ORM cascade tries to null out user_id instead of deleting the
    # row and fails — same issue delete_host already works around for
    # ZAHostGroupMember. Delete them explicitly first.
    roles_result = await session.execute(select(ZAUserRole).where(ZAUserRole.user_id == user_id))
    for row in roles_result.scalars().all():
        await session.delete(row)
    members_result = await session.execute(select(ZAUserGroupMember).where(ZAUserGroupMember.user_id == user_id))
    for row in members_result.scalars().all():
        await session.delete(row)

    await session.delete(user)
    await session.flush()
    # A deleted user may have been someone's only path to superadmin via a group grant
    # they were the last active member of — guard before commit, same as update_user.
    await _guard_last_superadmin(session)
    await session.commit()

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="user.delete",
        resource_type="user", resource_id=user_id, details={"username": username},
    )


@router.post("/users/{user_id}/deprovision", status_code=status.HTTP_204_NO_CONTENT)
async def deprovision_user(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
    actor_scopes: str = Header(default="", alias="X-User-Scopes"),
):
    """PCI DSS Phase C — deprovisioning webhook (checklist item: "external trigger
    (HR system, ticketing) -> immediate account disable, target <=24h SLA"). Gated
    on the caller's PAT carrying the "deprovision" scope specifically (not just
    require_admin) — an HR/ticketing integration authenticates with a dedicated
    token scoped to nothing but this action, auditable and revocable independently
    of any admin's own session. (The underlying account a scoped PAT is issued
    against must still hold a role with "users" write capability at the
    api-gateway RBAC layer — same as any PAT — this check narrows what that one
    token can actually do once a request reaches here.)"""
    if "deprovision" not in [s.strip() for s in actor_scopes.split(",") if s.strip()]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="token missing required 'deprovision' scope")

    result = await session.execute(select(ZAUser).where(ZAUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    user.is_active = False
    await session.flush()
    await _guard_last_superadmin(session)
    await session.commit()

    # Kill every active session immediately — best-effort, must never fail the
    # deprovision action itself if terminal-service is briefly unreachable (the
    # account is already disabled either way; a stray live session would still
    # be blocked from any NEW authorized action, just not forcibly disconnected).
    settings = get_settings()
    try:
        tok = mint("identity-service", "terminal-service", settings.service_jwt_secret)
        async with httpx.AsyncClient(base_url=settings.terminal_service_url, timeout=5.0) as client:
            await client.post(
                "/api/v1/internal/ssh/sessions/terminate-by-user",
                params={"target_user_id": user_id},
                headers={"X-Service-Token": tok},
            )
    except httpx.HTTPError:
        pass

    await log_action(
        session, user_id=actor_id or "external", username=actor_name or "deprovisioning-webhook",
        action="user.deprovision", resource_type="user", resource_id=user_id,
        details={"username": user.username}, result="success",
    )


@router.post("/users/{user_id}/mfa/reset", dependencies=[Depends(require_superadmin)])
async def reset_user_mfa(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    """Recovery path for a lost authenticator/mailbox: clears the target's MFA
    enrollment entirely so they can re-enroll from scratch. require_superadmin
    checks the literal X-User-Role header, which is safe here (not the vouched-role
    bypass fixed elsewhere this session) because api-gateway's downstream_role()
    only ever elevates a write up to "admin", never "superadmin" — so a genuine
    X-User-Role: superadmin can only come from an actual superadmin session."""
    result = await session.execute(select(ZAUser).where(ZAUser.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    user.totp_secret = ""
    user.totp_enabled = False
    user.mfa_method = None
    await session.commit()

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="user.mfa_reset",
        resource_type="user", resource_id=user_id, details={"username": user.username},
    )
    return {"mfa_method": None}


@router.get("/users/groups", response_model=list[UserGroupOut])
async def list_groups(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAUserGroup))
    return result.scalars().all()


@router.post("/users/groups", response_model=UserGroupOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_group(payload: UserGroupCreate, session: AsyncSession = Depends(get_session)):
    if (await session.execute(select(ZAUserGroup).where(ZAUserGroup.name == payload.name))).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="group name already exists")
    group = ZAUserGroup(name=payload.name, description=payload.description)
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


@router.put("/users/groups/{group_id}", response_model=UserGroupOut, dependencies=[Depends(require_admin)])
async def update_group(group_id: str, payload: UserGroupCreate, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    dup = await session.execute(select(ZAUserGroup).where(ZAUserGroup.name == payload.name, ZAUserGroup.id != group_id))
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="group name already exists")
    # zabbix_usrgrpid is sync-managed and not part of this payload — never touched here.
    group.name = payload.name
    group.description = payload.description
    await session.commit()
    await session.refresh(group)
    return group


@router.delete("/users/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_superadmin)])
async def delete_group(
    group_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    if group.zabbix_usrgrpid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Zabbix-synced groups cannot be deleted from SeyalRun — remove the group in Zabbix and re-sync instead",
        )

    name = group.name

    # ZAGroupRole and ZAUserGroupMember are association objects with composite
    # primary keys — same explicit-delete workaround as delete_user/delete_host.
    grouproles_result = await session.execute(select(ZAGroupRole).where(ZAGroupRole.group_id == group_id))
    for row in grouproles_result.scalars().all():
        await session.delete(row)
    members_result = await session.execute(select(ZAUserGroupMember).where(ZAUserGroupMember.group_id == group_id))
    for row in members_result.scalars().all():
        await session.delete(row)

    await session.delete(group)
    await session.flush()
    # A deleted group may have carried a superadmin grant to its last active member.
    await _guard_last_superadmin(session)
    await session.commit()

    await log_action(
        session, user_id=actor_id, username=actor_name or "", action="usergroup.delete",
        resource_type="user_group", resource_id=group_id, details={"name": name},
    )


@router.post("/users/sync-from-zabbix", dependencies=[Depends(require_admin)])
async def sync_users_from_zabbix(session: AsyncSession = Depends(get_session)):
    """Pull Zabbix usergroups + users into SeyalRun — same upsert-by-Zabbix-id pattern as
    inventory-service's host sync. Groups upsert by zabbix_usrgrpid, users by zabbix_userid.

    Provisions REAL login accounts for new users (shipped default password, forced change
    at first login — same handover pattern as the seeded admin). Sync NEVER touches an
    EXISTING user's password, role, or active flag, and NEVER assigns a role automatically —
    role assignment (direct, or via a synced group) stays an explicit, separate admin action.
    Group membership sync is additive only, mirroring host-group sync: it never removes a
    membership an admin set up by hand.
    """
    from .._pwpolicy import DEFAULT_SEED_PASSWORD
    from .settings import get_integration_value

    integration = await get_integration_value(session)
    zbx_api_url = (integration.get("zabbix_api_url") or "").strip()
    zbx_api_token = (integration.get("zabbix_api_token") or "").strip()
    if not zbx_api_url or not zbx_api_token:
        return {"synced_users": 0, "synced_groups": 0, "skipped": 0,
                "note": "Zabbix API URL/token not configured (set them in Settings → Integration)"}

    api_url = zbx_api_url.rstrip("/") + "/api_jsonrpc.php"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {zbx_api_token}"}

    async with httpx.AsyncClient(timeout=15) as client:
        gresp = await client.post(api_url, headers=headers, json={
            "jsonrpc": "2.0", "method": "usergroup.get", "id": 1,
            "params": {"output": ["usrgrpid", "name"]},
        })
        gresp.raise_for_status()
        gdata = gresp.json()
        if "error" in gdata:
            raise HTTPException(status_code=502, detail=f"Zabbix API error: {gdata['error'].get('data', gdata['error'])}")

        uresp = await client.post(api_url, headers=headers, json={
            "jsonrpc": "2.0", "method": "user.get", "id": 2,
            "params": {"output": ["userid", "username"], "selectUsrgrps": ["usrgrpid"]},
        })
        uresp.raise_for_status()
        udata = uresp.json()
        if "error" in udata:
            raise HTTPException(status_code=502, detail=f"Zabbix API error: {udata['error'].get('data', udata['error'])}")

    zbx_groups = gdata.get("result", [])
    zbx_users = udata.get("result", [])

    # 1. Upsert usergroups by zabbix_usrgrpid — adopt a same-named existing group rather
    # than duplicate it (matches the host-group sync precedent in inventory-service).
    group_by_zid: dict[str, ZAUserGroup] = {}
    synced_groups = 0
    for zg in zbx_groups:
        result = await session.execute(select(ZAUserGroup).where(ZAUserGroup.zabbix_usrgrpid == zg["usrgrpid"]))
        grp = result.scalar_one_or_none()
        if grp:
            grp.name = zg["name"]
        else:
            existing = await session.execute(select(ZAUserGroup).where(ZAUserGroup.name == zg["name"]))
            grp = existing.scalar_one_or_none()
            if grp:
                grp.zabbix_usrgrpid = zg["usrgrpid"]
            else:
                grp = ZAUserGroup(name=zg["name"], zabbix_usrgrpid=zg["usrgrpid"])
                session.add(grp)
        await session.flush()
        group_by_zid[zg["usrgrpid"]] = grp
        synced_groups += 1

    # 2. Upsert users by zabbix_userid, skipping Zabbix's built-in "guest" pseudo-account.
    # Adopting a same-named existing account (rather than duplicating) also self-heals the
    # separate zabbix_sso plugin, which stores the Zabbix *username* in this same column —
    # syncing corrects it to the real numeric Zabbix userid going forward.
    synced_users, skipped = 0, 0
    new_accounts: list[dict] = []
    for zu in zbx_users:
        if zu.get("username") == "guest":
            skipped += 1
            continue
        result = await session.execute(select(ZAUser).where(ZAUser.zabbix_userid == zu["userid"]))
        user = result.scalar_one_or_none()
        if user is None:
            existing = await session.execute(select(ZAUser).where(ZAUser.username == zu["username"]))
            user = existing.scalar_one_or_none()
            if user:
                user.zabbix_userid = zu["userid"]
            else:
                user = ZAUser(
                    username=zu["username"],
                    display_name=zu["username"],
                    zabbix_userid=zu["userid"],
                    password_hash=hash_password(DEFAULT_SEED_PASSWORD),
                    must_change_password=True,
                    is_active=True,
                )
                session.add(user)
                new_accounts.append({"username": zu["username"], "default_password": DEFAULT_SEED_PASSWORD})
            await session.flush()

        # 3. Group membership — additive only.
        for zg in (zu.get("usrgrps") or []):
            grp = group_by_zid.get(zg["usrgrpid"])
            if grp is None:
                continue
            link = await session.execute(
                select(ZAUserGroupMember).where(
                    ZAUserGroupMember.user_id == user.id, ZAUserGroupMember.group_id == grp.id,
                )
            )
            if not link.scalar_one_or_none():
                session.add(ZAUserGroupMember(user_id=user.id, group_id=grp.id))
        synced_users += 1

    await session.commit()
    return {
        "synced_users": synced_users, "synced_groups": synced_groups, "skipped": skipped,
        "new_accounts": new_accounts,
    }


@router.get("/users/groups/{group_id}/members")
async def get_group_members(group_id: str, session: AsyncSession = Depends(get_session)):
    if (await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")
    rows = await session.execute(select(ZAUserGroupMember.user_id).where(ZAUserGroupMember.group_id == group_id))
    return [{"user_id": uid} for (uid,) in rows.all()]


@router.put("/users/groups/{group_id}/members", dependencies=[Depends(require_admin)])
async def set_group_members(
    group_id: str,
    payload: GroupMembersUpdate,
    session: AsyncSession = Depends(get_session),
    actor_role: str = Header(default="user", alias="X-User-Role"),
):
    result = await session.execute(select(ZAUserGroup).where(ZAUserGroup.id == group_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")

    # Membership IS a privilege vector (group→role). If this group grants superadmin,
    # only a superadmin may change who's in it — else an admin could add themselves and
    # inherit superadmin, or remove the last superadmin-granting member and cause lockout.
    sid = await _superadmin_role_id(session)
    grants_super = sid is not None and (await session.execute(
        select(ZAGroupRole).where(ZAGroupRole.group_id == group_id, ZAGroupRole.role_id == sid)
    )).scalar_one_or_none() is not None
    if grants_super and actor_role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="only a superadmin may change members of a superadmin-granting group")

    existing = await session.execute(select(ZAUserGroupMember).where(ZAUserGroupMember.group_id == group_id))
    for member in existing.scalars().all():
        await session.delete(member)
    await session.flush()
    for user_id in dict.fromkeys(payload.user_ids):
        session.add(ZAUserGroupMember(user_id=user_id, group_id=group_id))
    await session.flush()
    await _guard_last_superadmin(session)  # raises 409 → not committed
    await session.commit()
    return {"group_id": group_id, "user_ids": payload.user_ids}
