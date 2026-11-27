from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..config import get_settings
from ..database import get_session
from ..deps import require_admin, require_service_token
from ..grouptree import descendants, parent_error
from ..models import ZACredential, ZACredentialHostLink, ZAHost, ZAHostGroup, ZAHostGroupMember
from ..schemas import HostAccountOut, HostCreate, HostGroupCreate, HostGroupOut, HostGroupUpdate, HostOut

router = APIRouter(tags=["hosts"], dependencies=[Depends(require_service_token)])


async def _host_out(session: AsyncSession, host: ZAHost) -> HostOut:
    result = await session.execute(select(ZAHostGroupMember.group_id).where(ZAHostGroupMember.host_id == host.id))
    group_ids = [g for (g,) in result.all()]
    return HostOut(
        id=host.id,
        zabbix_hostid=host.zabbix_hostid,
        name=host.name,
        ip=host.ip,
        port=host.port,
        os_type=host.os_type,
        host_type=host.host_type,
        gateway_order=host.gateway_order,
        enabled=host.enabled,
        zone_id=host.zone_id,
        last_synced_at=host.last_synced_at,
        is_reachable=host.is_reachable,
        last_ping_at=host.last_ping_at,
        created_at=host.created_at,
        group_ids=group_ids,
        is_production=host.is_production,
    )


@router.get("/hosts", response_model=list[HostOut])
async def list_hosts(
    session: AsyncSession = Depends(get_session),
    ids: str | None = Query(default=None, description="Comma-separated host ids to filter by (PAM enforcement)"),
):
    stmt = select(ZAHost)
    if ids is not None:
        id_list = [i for i in ids.split(",") if i]
        stmt = stmt.where(ZAHost.id.in_(id_list or [""]))
    result = await session.execute(stmt)
    return [await _host_out(session, h) for h in result.scalars().all()]


@router.post("/hosts", response_model=HostOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_host(
    payload: HostCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    # An asset must belong to at least one group. Group membership is how
    # authorization is granted at scale — a host in no group can only ever be
    # authorized one row at a time, and in practice is the host somebody forgets
    # to grant and then cannot reach. Validated BEFORE the row is created, so a
    # rejected create leaves nothing behind.
    if not payload.group_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one asset group is required — groups are how access is granted",
        )
    known = await session.execute(
        select(ZAHostGroup.id).where(ZAHostGroup.id.in_(payload.group_ids))
    )
    missing = set(payload.group_ids) - {g for (g,) in known.all()}
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown asset group(s): {sorted(missing)}",
        )

    host = ZAHost(
        name=payload.name,
        ip=payload.ip,
        port=payload.port,
        os_type=payload.os_type,
        host_type=payload.host_type,
        gateway_order=payload.gateway_order,
        enabled=payload.enabled,
        zone_id=payload.zone_id,
        zabbix_hostid=payload.zabbix_hostid,
        is_production=payload.is_production,
    )
    session.add(host)
    await session.flush()

    for group_id in payload.group_ids:
        session.add(ZAHostGroupMember(host_id=host.id, group_id=group_id))

    await session.commit()
    await session.refresh(host)

    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="host.create",
        resource_type="host", resource_id=host.id, details={"name": host.name, "ip": host.ip},
        result="success",
    )
    return await _host_out(session, host)


@router.get("/hosts/{host_id}", response_model=HostOut)
async def get_host(host_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAHost).where(ZAHost.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    return await _host_out(session, host)


@router.get("/internal/host-group-members")
async def internal_host_group_members(
    group_ids: str = Query(default="", description="comma-separated host group ids"),
    session: AsyncSession = Depends(get_session),
):
    """Host ids belonging to the given groups.

    Authorization lives in identity-service but GROUP MEMBERSHIP lives here, so
    identity had no way to answer "is this host in that group". It guessed in two
    different directions: authz/host-ids never expanded groups at all (so a grant
    made through a host group authorized nothing), while authz/resolve treated
    "this authorization names some group" as "it covers this host" (so it matched
    hosts that were in no granted group). This endpoint is what lets both ask
    instead of guess.
    """
    ids = [g.strip() for g in (group_ids or "").split(",") if g.strip()]
    if not ids:
        return {"host_ids": []}
    # Host tree: a grant on a group covers every group below it. The whole group
    # table is read once and walked in memory (app/grouptree.py) — one query no
    # matter how deep the tree, identical on PostgreSQL and MySQL — and the member
    # lookup stays a single IN query. Every authorization caller (identity's
    # authz/host-ids and authz/resolve, hence terminal login, SFTP, credential
    # reveal, the gateway host filter and posture) reaches group membership
    # through this endpoint, so expanding here is what makes them all agree.
    expanded = descendants(await _group_parents(session), ids)
    result = await session.execute(
        select(ZAHostGroupMember.host_id).where(ZAHostGroupMember.group_id.in_(sorted(expanded)))
    )
    return {"host_ids": sorted({h for (h,) in result.all()})}


@router.get("/internal/hosts/{host_id}/accounts")
async def internal_host_accounts(host_id: str, session: AsyncSession = Depends(get_session)):
    """The logins on one host, for the access-request picker in identity-service.

    Identity, name and username ONLY — the response model has no field a secret
    could ride in, and nothing here decrypts. Service-token gated (router
    dependency); identity decides who may ask.
    """
    host = await session.get(ZAHost, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    rows = await session.execute(
        select(ZACredential.id, ZACredential.name, ZACredential.username)
        .join(ZACredentialHostLink, ZACredentialHostLink.credential_id == ZACredential.id)
        .where(ZACredentialHostLink.host_id == host_id)
    )
    accounts = [HostAccountOut(id=cid, name=name or username, username=username).model_dump()
                for cid, name, username in rows.all()]
    accounts.sort(key=lambda a: (a["username"].lower(), a["name"].lower()))
    return {"host": {"id": host.id, "name": host.name}, "accounts": accounts}


async def _group_parents(session: AsyncSession) -> dict[str, str | None]:
    """{group_id: parent_id} for every group — one query."""
    rows = await session.execute(select(ZAHostGroup.id, ZAHostGroup.parent_id))
    return {gid: parent for gid, parent in rows.all()}


@router.get("/internal/hosts/{host_id}")
async def get_host_internal(host_id: str, session: AsyncSession = Depends(get_session)):
    """Host lookup for automation/terminal executors. Returns both v2 field names
    (name/ip/port) and the hostname/ip_address/ssh_port aliases the executors expect."""
    result = await session.execute(select(ZAHost).where(ZAHost.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    return {
        "id": host.id,
        "name": host.name,
        # Connection target is the IP/address field; aliased so executors that read
        # hostname/ip_address both connect to the right place (not the display name).
        "hostname": host.ip,
        "ip": host.ip,
        "ip_address": host.ip,
        "port": host.port,
        "ssh_port": host.port,
        "os_type": host.os_type,
        "enabled": host.enabled,
        "zone_id": host.zone_id,
        "is_production": host.is_production,
        "zabbix_hostid": host.zabbix_hostid,
    }


@router.post("/internal/hosts/check-production")
async def check_hosts_production(host_ids: list[str], session: AsyncSession = Depends(get_session)):
    """Bulk production-flag lookup for job-run gating (PCI DSS Phase D —
    automation-service forces approval when any target host is production)."""
    if not host_ids:
        return {"production_host_ids": []}
    result = await session.execute(
        select(ZAHost.id).where(ZAHost.id.in_(host_ids), ZAHost.is_production.is_(True))
    )
    return {"production_host_ids": [hid for (hid,) in result.all()]}


@router.post("/hosts/{host_id}/test")
async def test_host_reachability(host_id: str, session: AsyncSession = Depends(get_session)):
    """TCP-connect to the host's SSH port to verify reachability. Updates is_reachable + last_ping_at."""
    result = await session.execute(select(ZAHost).where(ZAHost.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")

    reachable = False
    error_msg = ""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host.ip, host.port or 22), timeout=5.0
        )
        writer.close()
        await writer.wait_closed()
        reachable = True
    except asyncio.TimeoutError:
        error_msg = "Connection timed out"
    except OSError as exc:
        error_msg = str(exc)

    now = datetime.now(timezone.utc)
    host.is_reachable = reachable
    host.last_ping_at = now
    await session.commit()

    return {"host_id": host_id, "reachable": reachable, "error": error_msg, "tested_at": now.isoformat()}


@router.put("/hosts/{host_id}", response_model=HostOut, dependencies=[Depends(require_admin)])
async def update_host(
    host_id: str,
    payload: HostCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZAHost).where(ZAHost.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")

    host.name = payload.name
    host.ip = payload.ip
    host.port = payload.port
    host.os_type = payload.os_type
    # Persisted on update too. Without these, editing any field of a gateway
    # silently demoted it back to a server — it would vanish from its zone's
    # chain, and the connection would start failing for a reason nothing in the
    # edit suggested.
    host.host_type = payload.host_type
    host.gateway_order = payload.gateway_order
    host.enabled = payload.enabled
    host.zone_id = payload.zone_id
    host.is_production = payload.is_production
    # zabbix_hostid is sync-managed, not an Assets form field — the edit UI never
    # sends it, so payload.zabbix_hostid defaults to None. Overwriting unconditionally
    # would silently unlink the host from Zabbix on every unrelated edit (rename,
    # credential change, zone move, ...), and the next sync would then create a
    # duplicate row instead of relinking. Preserve the existing value unless a
    # caller explicitly sends one.
    if payload.zabbix_hostid is not None:
        host.zabbix_hostid = payload.zabbix_hostid

    existing = await session.execute(select(ZAHostGroupMember).where(ZAHostGroupMember.host_id == host_id))
    for member in existing.scalars().all():
        await session.delete(member)
    for group_id in payload.group_ids:
        session.add(ZAHostGroupMember(host_id=host_id, group_id=group_id))

    await session.commit()
    await session.refresh(host)

    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="host.update",
        resource_type="host", resource_id=host.id,
        result="success",
    )
    return await _host_out(session, host)


@router.delete("/hosts/{host_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_host(
    host_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    result = await session.execute(select(ZAHost).where(ZAHost.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    if host.zabbix_hostid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Zabbix-synced hosts cannot be deleted from SeyalRun — remove the host in Zabbix and re-sync instead",
        )

    # ZAHostGroupMember is an association *object* (not a plain secondary= table),
    # so SQLAlchemy won't auto-cascade its rows on host delete — it tries to null
    # out host_id instead, which fails since that column is part of the composite
    # primary key. Delete membership rows explicitly first (same pattern update_host
    # already uses).
    members = await session.execute(select(ZAHostGroupMember).where(ZAHostGroupMember.host_id == host_id))
    for member in members.scalars().all():
        await session.delete(member)

    await session.delete(host)
    await session.commit()

    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="host.delete",
        resource_type="host", resource_id=host_id,
        result="success",
    )


@router.get("/host-groups", response_model=list[HostGroupOut])
async def list_host_groups(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAHostGroup))
    return result.scalars().all()


@router.get("/host-groups/{group_id}", response_model=HostGroupOut)
async def get_host_group(group_id: str, session: AsyncSession = Depends(get_session)):
    group = await session.get(ZAHostGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host group not found")
    return group


@router.post("/host-groups", response_model=HostGroupOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_host_group(
    payload: HostGroupCreate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    existing = await session.execute(select(ZAHostGroup).where(ZAHostGroup.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="host group name already exists")
    payload.parent_id = payload.parent_id or None
    err = parent_error(await _group_parents(session), None, payload.parent_id)
    if err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
    group = ZAHostGroup(**payload.model_dump())
    session.add(group)
    await session.commit()
    await session.refresh(group)
    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="host_group.create",
        resource_type="host_group", resource_id=group.id,
        details={"name": group.name, "parent_id": group.parent_id}, result="success",
    )
    return group


@router.put("/host-groups/{group_id}", response_model=HostGroupOut, dependencies=[Depends(require_admin)])
async def update_host_group(
    group_id: str,
    payload: HostGroupUpdate,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    """Rename, describe, or move a group in the host tree.

    Moving a group changes what every grant on its old and new ancestors covers,
    so the move is validated against cycles and depth here (with create, the only
    places a parent can be set) and audited with both parents."""
    group = await session.get(ZAHostGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host group not found")
    fields = payload.model_fields_set
    if "name" in fields and payload.name is not None and payload.name != group.name:
        clash = await session.execute(
            select(ZAHostGroup.id).where(ZAHostGroup.name == payload.name, ZAHostGroup.id != group_id)
        )
        if clash.first():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="host group name already exists")
        group.name = payload.name
    if "description" in fields and payload.description is not None:
        group.description = payload.description
    old_parent = group.parent_id
    if "parent_id" in fields:
        new_parent = payload.parent_id or None
        err = parent_error(await _group_parents(session), group_id, new_parent)
        if err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
        group.parent_id = new_parent
    await session.commit()
    await session.refresh(group)
    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="host_group.update",
        resource_type="host_group", resource_id=group.id,
        details={"name": group.name, "parent_id": group.parent_id, "previous_parent_id": old_parent},
        result="success",
    )
    return group


@router.delete("/host-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_host_group(
    group_id: str,
    session: AsyncSession = Depends(get_session),
    actor_id: str | None = Header(default=None, alias="X-User-Id"),
    actor_name: str | None = Header(default=None, alias="X-User-Name"),
):
    group = await session.get(ZAHostGroup, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host group not found")
    # Refused rather than re-parenting or cascading: either would silently change
    # what grants on this group's ancestors cover, and deleting one group should
    # not be how that gets decided for a whole subtree.
    child = await session.execute(
        select(ZAHostGroup.name).where(ZAHostGroup.parent_id == group_id).order_by(ZAHostGroup.name).limit(1)
    )
    first_child = child.scalar_one_or_none()
    if first_child is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"host group '{group.name}' still has child groups (e.g. '{first_child}') — "
                   "move or delete them first",
        )
    # Association object, not a secondary= table — membership rows go explicitly
    # (same reason as delete_host).
    members = await session.execute(select(ZAHostGroupMember).where(ZAHostGroupMember.group_id == group_id))
    for member in members.scalars().all():
        await session.delete(member)
    name = group.name
    await session.delete(group)
    await session.commit()
    await audit.log_action(
        user_id=actor_id, username=actor_name or "", action="host_group.delete",
        resource_type="host_group", resource_id=group_id, details={"name": name}, result="success",
    )


async def _resolve_zabbix_creds(settings) -> tuple[str, str]:
    """(api_url, api_token) — the superadmin-editable DB integration setting (what the
    UI saves) WINS over .env, so a token entered in the console actually drives the sync.
    Mirrors the gateway's _integration_settings resolution."""
    from libs.servicetoken import mint
    db_url, db_token = "", ""
    try:
        tok = mint("inventory-service", "identity-service", settings.service_jwt_secret)
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(
                f"{settings.identity_service_url}/api/v1/internal/settings/integration",
                headers={"X-Service-Token": tok},
            )
            if r.status_code == 200:
                d = r.json()
                db_url = (d.get("zabbix_api_url") or "").strip()
                db_token = (d.get("zabbix_api_token") or "").strip()
    except httpx.HTTPError:
        pass
    return (db_url or (settings.zabbix_api_url or "").strip(),
            db_token or (settings.zabbix_api_token or "").strip())


@router.post("/hosts/sync-from-zabbix", dependencies=[Depends(require_admin)])
async def sync_from_zabbix(session: AsyncSession = Depends(get_session)):
    settings = get_settings()
    zbx_api_url, zbx_api_token = await _resolve_zabbix_creds(settings)
    if not zbx_api_url or not zbx_api_token:
        return {"synced": 0, "skipped": 0, "note": "Zabbix API URL/token not configured (set them in Settings → Integration)"}

    api_url = zbx_api_url.rstrip("/") + "/api_jsonrpc.php"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {zbx_api_token}",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        # Fetch hosts with their primary agent interface
        resp = await client.post(api_url, headers=headers, json={
            "jsonrpc": "2.0", "method": "host.get", "id": 1,
            "params": {
                "output": ["hostid", "host", "name", "status"],
                "selectInterfaces": ["ip", "port", "type", "main"],
                # Zabbix 7.0 removed `selectGroups` in favour of `selectHostGroups`
                # (returns the data under "hostgroups").
                "selectHostGroups": ["groupid", "name"],
            },
        })
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise HTTPException(status_code=502, detail=f"Zabbix API error: {data['error'].get('data', data['error'])}")

    zbx_hosts = data.get("result", [])
    synced, skipped = 0, 0
    now = datetime.now(timezone.utc)

    for zh in zbx_hosts:
        if zh.get("status") != "0":   # skip disabled hosts
            skipped += 1
            continue

        # Pick the primary agent interface IP; fall back to first interface
        ifaces = zh.get("interfaces", [])
        primary = next((i for i in ifaces if i.get("main") == "1" and i.get("type") == "1"), None) \
                  or next((i for i in ifaces if i.get("main") == "1"), None) \
                  or (ifaces[0] if ifaces else None)
        ip = primary["ip"] if primary and primary.get("ip") else ""
        if not ip:
            skipped += 1
            continue

        # Upsert host
        result = await session.execute(
            select(ZAHost).where(ZAHost.zabbix_hostid == zh["hostid"])
        )
        host = result.scalar_one_or_none()
        if host:
            host.name = zh.get("name") or zh["host"]
            host.ip = ip
            host.last_synced_at = now
        else:
            host = ZAHost(
                id=str(uuid.uuid4()),
                zabbix_hostid=zh["hostid"],
                name=zh.get("name") or zh["host"],
                ip=ip,
                last_synced_at=now,
            )
            session.add(host)
        await session.flush()

        # Sync host groups (Zabbix 7.0 returns "hostgroups"; older returns "groups")
        for zg in (zh.get("hostgroups") or zh.get("groups") or []):
            grp_result = await session.execute(
                select(ZAHostGroup).where(ZAHostGroup.name == zg["name"])
            )
            grp = grp_result.scalar_one_or_none()
            if not grp:
                grp = ZAHostGroup(id=str(uuid.uuid4()), name=zg["name"])
                session.add(grp)
                await session.flush()

            link_result = await session.execute(
                select(ZAHostGroupMember).where(
                    ZAHostGroupMember.host_id == host.id,
                    ZAHostGroupMember.group_id == grp.id,
                )
            )
            if not link_result.scalar_one_or_none():
                session.add(ZAHostGroupMember(host_id=host.id, group_id=grp.id))

        synced += 1

    await session.commit()
    return {"synced": synced, "skipped": skipped}
