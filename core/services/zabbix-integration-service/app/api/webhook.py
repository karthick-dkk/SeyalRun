"""Zabbix webhook + execute receivers.

Two HMAC-authenticated entry points, both bypassing the api-gateway (edge-proxy
routes /webhook/ straight here, so HMAC is their only authentication):

- POST /webhook/zabbix          — fired automatically by a Zabbix Action when a
                                   trigger matches; auto-dispatches bound playbooks.
- POST /webhook/zabbix/execute  — invoked on demand from the Zabbix Problems screen
                                   (a "Webhook" global script); runs the pre-bound
                                   playbook for the affected host and posts the
                                   COMPLETE output back into the Problem.

Both only ever run playbooks an admin has explicitly bound to a trigger/host-group
in SeyalRun (pre-bound allowlist), and the automation-service filters caller params
server-side — so a Zabbix payload can never choose an arbitrary template or inject
playbook code.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import ZAZabbixTriggerBinding

router = APIRouter()
logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None

# Zabbix caps an event.acknowledge message at 2048 chars; keep a margin for the
# per-message header we prepend.
_ZBX_MSG_LIMIT = 1900
_MAX_POSTBACK_CHUNKS = 25   # ~47 KB of log; beyond this we truncate + point at SeyalRun


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


# ── Shared auth / guards ─────────────────────────────────────────────────────

async def _authenticate(request: Request, raw_body: bytes, settings) -> str:
    """HMAC + optional IP allowlist + per-source rate limit + replay protection.
    Returns the client IP. Raises HTTPException on any failure. Shared by both the
    auto webhook and the on-demand execute endpoint so they enforce identical auth."""
    secret = settings.zabbix_webhook_hmac_secret
    if not secret:
        logger.error("webhook rejected: ZABBIX_WEBHOOK_HMAC_SECRET is not configured")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="webhook authentication not configured")
    sig = request.headers.get("X-SeyalRun-Signature", "")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid HMAC signature")

    redis = _get_redis()
    client_ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "")

    allowlist = [ip.strip() for ip in settings.zabbix_webhook_ip_allowlist.split(",") if ip.strip()]
    if allowlist and client_ip not in allowlist:
        logger.warning("webhook rejected: source IP not allowlisted", extra={"ip": client_ip})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="source not allowed")

    rl_key = f"zbx:rl:{client_ip}:{int(time.time() // 60)}"
    count = await redis.incr(rl_key)
    if count == 1:
        await redis.expire(rl_key, 60)
    if count > settings.webhook_rate_limit_per_minute:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate limit exceeded")

    # Replay protection: an identical signed body seen within the window is rejected.
    if not await redis.set(f"zbx:replay:{sig}", "1", nx=True, ex=settings.webhook_replay_window_seconds):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="replay detected")
    return client_ip


def _parse_event(raw_body: bytes) -> dict:
    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")
    return payload


def _match_bindings(bindings, severity: int, triggerid: str, host_groups: list) -> list:
    """Pre-bound allowlist: a Zabbix event runs a playbook only when an admin has
    bound this trigger id / host-group at or above this severity."""
    matched = []
    for b in bindings:
        if severity < b.severity_min:
            continue
        if b.zabbix_triggerid and b.zabbix_triggerid != triggerid:
            continue
        if b.zabbix_host_group and b.zabbix_host_group not in host_groups:
            continue
        matched.append(b)
    return matched


async def _load_enabled_bindings() -> list:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ZAZabbixTriggerBinding).where(ZAZabbixTriggerBinding.enabled.is_(True))
        )
        return list(result.scalars().all())


async def _resolve_template_name(template_id: str, settings) -> str:
    """Human name of a job template (best-effort) so the Problem shows WHICH playbook ran."""
    from libs.servicetoken import mint
    if not template_id:
        return ""
    try:
        async with httpx.AsyncClient(base_url=settings.automation_service_url, timeout=6) as client:
            tok = mint("zabbix-integration-service", "automation-service", settings.service_jwt_secret)
            resp = await client.get(f"/api/v1/job-templates/{template_id}", headers={"X-Service-Token": tok})
            if resp.status_code == 200:
                return resp.json().get("name", "")
    except Exception:
        pass
    return ""


async def _playbook_label(binding, settings) -> str:
    """'<binding name> · <template name>' — what actually executed, for the post-back."""
    tmpl = await _resolve_template_name(binding.job_template_id, settings)
    return f"{binding.name} · {tmpl}" if tmpl else binding.name


async def _resolve_affected_host_ids(hostname: str, settings) -> list[str]:
    from libs.servicetoken import mint
    if not hostname:
        return []
    try:
        async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=10) as client:
            tok = mint("zabbix-integration-service", "inventory-service", settings.service_jwt_secret)
            resp = await client.get("/api/v1/hosts", params={"name": hostname},
                                    headers={"X-Service-Token": tok})
            if resp.status_code == 200:
                hosts = resp.json()
                return [h["id"] for h in hosts
                        if h.get("name") == hostname or h.get("hostname") == hostname]
    except Exception as e:
        logger.warning("host lookup failed for %s: %s", hostname, e)
    return []


async def _dispatch_run(binding, target_host_ids: list[str], triggered_by: str,
                        extra: dict, settings) -> str | None:
    """Kick off one automation run for a binding. Returns the run_id or None."""
    from libs.servicetoken import mint
    run_params = {**binding.extra_vars, **(extra or {})}
    try:
        async with httpx.AsyncClient(base_url=settings.automation_service_url, timeout=30) as client:
            tok = mint("zabbix-integration-service", "automation-service", settings.service_jwt_secret)
            resp = await client.post(
                "/api/v1/internal/job-runs",
                json={
                    "job_template_id": binding.job_template_id,
                    "triggered_by": triggered_by,
                    "params": run_params,
                    "target_host_ids": target_host_ids,
                },
                headers={"X-Service-Token": tok},
            )
            if resp.status_code not in (200, 202):
                logger.warning("automation dispatch failed: %s", resp.text)
                return None
            return resp.json().get("run_id")
    except Exception as e:
        logger.warning("automation dispatch error: %s", e)
        return None


# ── Auto webhook (Zabbix Action) ─────────────────────────────────────────────

@router.post("/webhook/zabbix", status_code=202)
async def zabbix_webhook(request: Request) -> dict:
    settings = get_settings()
    raw_body = await request.body()
    await _authenticate(request, raw_body, settings)
    payload = _parse_event(raw_body)

    event_id = str(payload.get("eventid", ""))
    severity = int(payload.get("severity", 0))
    triggerid = str(payload.get("triggerid", ""))
    hostname = str(payload.get("hostname", ""))
    host_groups = payload.get("host_groups", [])

    # Redis-backed dedup — Zabbix may re-send on problem updates.
    redis = _get_redis()
    if event_id:
        dedup_key = f"zbx:dedup:{event_id}"
        if await redis.exists(dedup_key):
            return {"status": "duplicate", "matched_bindings": 0}
        await redis.setex(dedup_key, 300, "1")

    bindings = _match_bindings(await _load_enabled_bindings(), severity, triggerid, host_groups)
    for binding in bindings:
        asyncio.create_task(_dispatch_binding(binding, payload, hostname, settings))
    return {"status": "accepted", "matched_bindings": len(bindings)}


async def _dispatch_binding(binding, payload: dict, hostname: str, settings) -> None:
    target_host_ids: list[str] = []
    if binding.target_scope == "affected_host":
        target_host_ids = await _resolve_affected_host_ids(hostname, settings)
    event_id = str(payload.get("eventid", "unknown"))
    run_id = await _dispatch_run(binding, target_host_ids, f"zabbix_event:{event_id}", {}, settings)
    if binding.post_result_to_zabbix and run_id and event_id != "unknown":
        playbook = await _playbook_label(binding, settings)
        asyncio.create_task(_post_result_to_zabbix(run_id, event_id, settings, playbook=playbook))


# ── On-demand execute (Zabbix Problems "Webhook" global script) ───────────────

@router.post("/webhook/zabbix/execute", status_code=202)
async def zabbix_execute(request: Request) -> dict:
    """Run the pre-bound remediation playbook for a Problem, on demand from the
    Zabbix UI, and stream the full output back into that Problem. Same HMAC auth
    as the auto webhook; only pre-bound trigger→playbook mappings are eligible."""
    settings = get_settings()
    raw_body = await request.body()
    await _authenticate(request, raw_body, settings)
    payload = _parse_event(raw_body)

    event_id = str(payload.get("eventid", ""))
    severity = int(payload.get("severity", 0))
    triggerid = str(payload.get("triggerid", ""))
    hostname = str(payload.get("hostname", ""))
    host_groups = payload.get("host_groups", [])
    selector = str(payload.get("binding", "")).strip()   # operator's playbook choice (id or name)
    if not event_id:
        raise HTTPException(status_code=400, detail="eventid required")

    # Debounce accidental double-clicks (short), but allow deliberate re-runs later.
    # Keyed by the chosen playbook too, so picking a different one isn't debounced away.
    redis = _get_redis()
    if not await redis.set(f"zbx:exec:{event_id}:{triggerid}:{selector}", "1", nx=True, ex=10):
        return {"status": "in_progress", "detail": "an execution for this problem just started"}

    enabled = await _load_enabled_bindings()
    if selector:
        # Explicit playbook chosen in Zabbix (a per-playbook script or a {MANUALINPUT}
        # dropdown). Still pre-bound: it must be one of the admin's enabled bindings.
        bindings = [b for b in enabled if b.id == selector or b.name == selector]
        if not bindings:
            raise HTTPException(status_code=404,
                                detail=f"no_binding: '{selector}' is not an enabled trigger binding")
    else:
        bindings = _match_bindings(enabled, severity, triggerid, host_groups)
        if not bindings:
            raise HTTPException(status_code=404,
                                detail="no_binding: no enabled trigger binding matches this problem")

    target_host_ids = await _resolve_affected_host_ids(hostname, settings)
    started = 0
    run_ids: list[str] = []
    for binding in bindings:
        run_id = await _dispatch_run(binding, target_host_ids,
                                     f"zabbix_manual:{event_id}", {}, settings)
        if run_id:
            started += 1
            run_ids.append(run_id)
            # Always post output back for an on-demand run — that's the whole point.
            playbook = await _playbook_label(binding, settings)
            asyncio.create_task(_post_result_to_zabbix(
                run_id, event_id, settings, header="▶ Manual remediation", playbook=playbook))
    if not started:
        raise HTTPException(status_code=502, detail="dispatch failed for all matched bindings")
    return {"status": "started", "runs": started, "run_ids": run_ids,
            "playbooks": [b.name for b in bindings]}


# ── Zabbix API creds + full-output post-back ─────────────────────────────────

async def _resolve_zabbix_creds(settings) -> tuple[str, str]:
    """(api_url, api_token) — DB integration setting (UI-configured) wins over .env, the
    same resolution the gateway + inventory host-sync use, so the acknowledge post-back
    authenticates with the token entered in the console instead of a blank env var."""
    from libs.servicetoken import mint
    db_url, db_token = "", ""
    try:
        tok = mint("zabbix-integration-service", "identity-service", settings.service_jwt_secret)
        async with httpx.AsyncClient(base_url=settings.identity_service_url, timeout=4.0) as client:
            r = await client.get("/api/v1/internal/settings/integration", headers={"X-Service-Token": tok})
            if r.status_code == 200:
                d = r.json()
                db_url = (d.get("zabbix_api_url") or "").strip()
                db_token = (d.get("zabbix_api_token") or "").strip()
    except httpx.HTTPError:
        pass
    return (db_url or (settings.zabbix_api_url or "").strip(),
            db_token or (settings.zabbix_api_token or "").strip())


def _chunk_output(lines: list[str]) -> list[str]:
    """Pack output lines into ≤_ZBX_MSG_LIMIT chunks, capped at _MAX_POSTBACK_CHUNKS."""
    chunks: list[str] = []
    buf = ""
    for ln in lines:
        ln = str(ln)
        # A single very long line is hard-split.
        while len(ln) > _ZBX_MSG_LIMIT:
            if buf:
                chunks.append(buf); buf = ""
            chunks.append(ln[:_ZBX_MSG_LIMIT]); ln = ln[_ZBX_MSG_LIMIT:]
        if len(buf) + len(ln) + 1 > _ZBX_MSG_LIMIT:
            chunks.append(buf); buf = ln
        else:
            buf = f"{buf}\n{ln}" if buf else ln
        if len(chunks) >= _MAX_POSTBACK_CHUNKS:
            break
    if buf and len(chunks) < _MAX_POSTBACK_CHUNKS:
        chunks.append(buf)
    return chunks


async def _zbx_acknowledge(client: httpx.AsyncClient, token: str, event_id: str, message: str) -> int:
    """Post one message onto a Zabbix event (action bit 4 = add message)."""
    resp = await client.post(
        "/api_jsonrpc.php",
        json={"jsonrpc": "2.0", "method": "event.acknowledge",
              "params": {"eventids": [event_id], "action": 4, "message": message[:2048]}, "id": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.status_code


async def _post_result_to_zabbix(run_id: str, event_id: str, settings,
                                 header: str = "▶ SeyalRun automation", playbook: str = "") -> None:
    """Wait for the run to finish, then write its COMPLETE output onto the Zabbix
    Problem as a sequence of acknowledge messages (chunked to fit Zabbix's limit)."""
    zbx_api_url, zbx_api_token = await _resolve_zabbix_creds(settings)
    if not zbx_api_url or not zbx_api_token:
        logger.info("zabbix post-back skipped: API URL/token not configured")
        return

    # Poll for completion, collecting the final run detail (incl. output_lines).
    from libs.servicetoken import mint
    run: dict = {}
    for _ in range(max(1, settings.job_exec_timeout_seconds // 2)):
        await asyncio.sleep(2)
        try:
            async with httpx.AsyncClient(base_url=settings.automation_service_url, timeout=10) as client:
                tok = mint("zabbix-integration-service", "automation-service", settings.service_jwt_secret)
                resp = await client.get(f"/api/v1/internal/job-runs/{run_id}", headers={"X-Service-Token": tok})
                if resp.status_code == 200:
                    run = resp.json()
                    if run.get("status") in ("success", "failed", "error", "cancelled"):
                        break
        except Exception:
            pass
    else:
        run = run or {"status": "timeout"}

    status_text = run.get("status", "unknown")
    exit_code = run.get("exit_code")
    output = run.get("output_lines") or []
    chunks = _chunk_output(output)
    total = len(chunks)

    try:
        async with httpx.AsyncClient(base_url=zbx_api_url, timeout=15) as client:
            head = (f"{header}\n"
                    f"{'playbook: ' + playbook + chr(10) if playbook else ''}"
                    f"run {run_id}\n"
                    f"status: {status_text}"
                    f"{'' if exit_code is None else f' (exit {exit_code})'}\n"
                    f"output: {len(output)} line(s)"
                    f"{'' if total <= _MAX_POSTBACK_CHUNKS else ' — truncated'}")
            code = await _zbx_acknowledge(client, zbx_api_token, event_id, head)
            if code >= 400:
                logger.warning("zabbix post-back header failed: HTTP %s", code)
            for i, chunk in enumerate(chunks, 1):
                await _zbx_acknowledge(client, zbx_api_token, event_id, f"[{i}/{total}]\n{chunk}")
                await asyncio.sleep(0.3)   # be gentle with the Zabbix API
            if len(output) > 0 and total >= _MAX_POSTBACK_CHUNKS:
                await _zbx_acknowledge(client, zbx_api_token, event_id,
                                       f"… output truncated. Full log: SeyalRun job run {run_id}")
        logger.info("zabbix post-back complete", extra={"run_id": run_id, "chunks": total, "status": status_text})
    except Exception as e:
        logger.warning("zabbix result posting failed: %s", e)
