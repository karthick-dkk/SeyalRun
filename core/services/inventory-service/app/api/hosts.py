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
from ..models import ZAHost, ZAHostGroup, ZAHostGroupMember
from ..schemas import HostCreate, HostGroupCreate, HostGroupOut, HostOut

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
    host = ZAHost(
        name=payload.name,
        ip=payload.ip,
        port=payload.port,
        os_type=payload.os_type,
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
    )
    return await _host_out(session, host)


@router.get("/hosts/{host_id}", response_model=HostOut)
async def get_host(host_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAHost).where(ZAHost.id == host_id))
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="host not found")
    return await _host_out(session, host)


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
    )


@router.get("/host-groups", response_model=list[HostGroupOut])
async def list_host_groups(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(ZAHostGroup))
    return result.scalars().all()


@router.post("/host-groups", response_model=HostGroupOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_host_group(payload: HostGroupCreate, session: AsyncSession = Depends(get_session)):
    existing = await session.execute(select(ZAHostGroup).where(ZAHostGroup.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="host group name already exists")
    group = ZAHostGroup(**payload.model_dump())
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


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
