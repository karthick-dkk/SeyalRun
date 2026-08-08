"""zbx-sync — bidirectional Zabbix ↔ JumpServer asset sync."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI
from pravesh_shared.health import register_readiness_check
from pravesh_shared.health import router as health_router
from pravesh_shared.log import configure_logging
from pravesh_shared.middleware import RequestContextMiddleware
from pravesh_shared.tls import verify_for
from prometheus_client import make_asgi_app

configure_logging("zbx-sync", os.getenv("ZS_LOG_LEVEL", "INFO"))
log = structlog.get_logger(__name__)

ZABBIX_URL = os.getenv("ZS_ZABBIX_API_URL", "http://192.168.64.8")
ZABBIX_TOKEN = os.getenv("ZS_ZABBIX_API_TOKEN", "")
ZABBIX_VERIFY = verify_for("ZS_ZABBIX_CA_BUNDLE")
JMS_URL = os.getenv("ZS_JUMPSERVER_API_URL", "http://192.168.64.2")
JMS_KEY = os.getenv("ZS_JUMPSERVER_API_KEY", "")
JMS_VERIFY = verify_for("ZS_JUMPSERVER_CA_BUNDLE")
SYNC_INTERVAL = int(os.getenv("ZS_SYNC_INTERVAL_MINUTES", "5")) * 60
REDIS_URL = os.getenv("ZS_REDIS_URL", "redis://localhost:6379/2")

_sync_task: asyncio.Task | None = None


async def check_zabbix() -> bool:
    async with httpx.AsyncClient(verify=ZABBIX_VERIFY, timeout=5) as client:
        r = await client.post(
            f"{ZABBIX_URL}/api_jsonrpc.php",
            json={"jsonrpc": "2.0", "method": "apiinfo.version", "params": [], "id": 1},
        )
        r.raise_for_status()
    return True


async def check_jumpserver() -> bool:
    async with httpx.AsyncClient(verify=JMS_VERIFY, timeout=5) as client:
        r = await client.get(f"{JMS_URL}/api/v1/settings/public/")
        assert r.status_code in (200, 401)
    return True


async def sync_loop() -> None:
    """Background sync loop."""
    while True:
        try:
            await run_sync(dry_run=False)
        except Exception as exc:
            log.error("sync_loop_error", error=str(exc))
        await asyncio.sleep(SYNC_INTERVAL)


async def run_sync(dry_run: bool = False) -> dict:
    """Sync Zabbix hosts → JumpServer assets."""
    log.info("sync_start", dry_run=dry_run)
    results = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}

    try:
        # 1. Get Zabbix hosts
        async with httpx.AsyncClient(verify=ZABBIX_VERIFY, timeout=30) as client:
            zbx_resp = await client.post(
                f"{ZABBIX_URL}/api_jsonrpc.php",
                json={
                    "jsonrpc": "2.0",
                    "method": "host.get",
                    "params": {
                        "output": ["hostid", "host", "name", "status"],
                        "selectInterfaces": ["ip", "port", "type"],
                        "filter": {"status": 0},
                    },
                    "auth": ZABBIX_TOKEN,
                    "id": 1,
                },
            )
            zbx_hosts = zbx_resp.json().get("result", [])

        # 2. Get JumpServer assets
        headers = {
            "Authorization": f"Token {JMS_KEY}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(verify=JMS_VERIFY, timeout=30) as client:
            jms_resp = await client.get(
                f"{JMS_URL}/api/v1/assets/assets/?type=linux",
                headers=headers,
            )
            jms_assets = {a["name"]: a for a in jms_resp.json().get("data", [])}

        # 3. Sync each Zabbix host to JumpServer
        for host in zbx_hosts:
            hostname = host.get("host", "")
            ifaces = host.get("interfaces", [])
            ip = ifaces[0]["ip"] if ifaces else "0.0.0.0"

            if dry_run:
                log.info("dry_run_would_sync", host=hostname, ip=ip)
                results["skipped"] += 1
                continue

            if hostname not in jms_assets:
                # Create asset
                async with httpx.AsyncClient(verify=JMS_VERIFY, timeout=15) as client:
                    r = await client.post(
                        f"{JMS_URL}/api/v1/assets/assets/",
                        headers=headers,
                        json={
                            "name": hostname,
                            "address": ip,
                            "platform": {"name": "Linux"},
                            "protocols": [{"name": "ssh", "port": 22}],
                            "comment": f"Synced from Zabbix hostid={host['hostid']}",
                        },
                    )
                    if r.status_code in (200, 201):
                        results["created"] += 1
                        log.info("asset_created", host=hostname, ip=ip)
                    else:
                        results["errors"] += 1
                        log.warning("asset_create_failed", host=hostname, status=r.status_code)
            else:
                results["skipped"] += 1

    except Exception as exc:
        log.error("sync_error", error=str(exc))
        results["errors"] += 1

    log.info("sync_complete", **results)
    return results


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _sync_task
    register_readiness_check("zabbix", check_zabbix)
    register_readiness_check("jumpserver", check_jumpserver)
    _sync_task = asyncio.create_task(sync_loop())
    log.info("zbx_sync_starting", interval_seconds=SYNC_INTERVAL)
    yield
    if _sync_task:
        _sync_task.cancel()


app = FastAPI(title="zbx-sync", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.include_router(health_router)
app.mount("/metrics", make_asgi_app())


@app.post("/sync/run")
async def trigger_sync(dry_run: bool = False) -> dict:
    return await run_sync(dry_run=dry_run)


@app.get("/sync/status")
async def sync_status() -> dict:
    return {
        "zabbix_url": ZABBIX_URL,
        "jumpserver_url": JMS_URL,
        "sync_interval_seconds": SYNC_INTERVAL,
        "sync_task_running": _sync_task is not None and not _sync_task.done(),
    }
