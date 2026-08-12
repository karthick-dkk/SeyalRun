"""Executor: push a managed account (user + password/ssh_key) onto target hosts."""

from __future__ import annotations

import asyncio
from typing import Callable

from libs.pluginbase import ActionExecutor, RunRequest, RunResult

from app._ssh_exec import run_command as _ssh_run


class AccountPushExecutor(ActionExecutor):
    name = "account_push"

    def validate(self, params: dict) -> None:
        if not params.get("subject_credential_id") and not params.get("subject_cred_id"):
            raise ValueError("account_push requires subject_credential_id in params")

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        from app.config import get_settings
        import httpx
        from libs.servicetoken import mint

        settings = get_settings()

        from app._account_ops import get_secret, get_host, host_admin_cred_id, is_privileged, sudo_exec

        subject_cred_id = request.params.get("subject_credential_id") or request.params.get("subject_cred_id")
        if not subject_cred_id:
            return RunResult(ok=False, output="account_push requires subject_credential_id", exit_code=1)

        async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=10) as client:
            tok = mint("automation-service", "inventory-service", settings.service_jwt_secret)

            subject = await get_secret(client, tok, subject_cred_id)
            if not subject:
                return RunResult(ok=False, output="subject credential lookup failed", exit_code=1)

            # Per host: resolve the privileged login — per-host override, then a single
            # shared credential, then the host's first linked credential ("default" =
            # the push account of the server) — so default templates work without a fixed admin.
            host_cred_map = request.params.get("_host_credentials") or {}
            targets = []
            for hid in request.target_host_ids:
                host = await get_host(client, tok, hid)
                if not host:
                    continue
                admin_id = host_cred_map.get(hid) or request.credential_id or await host_admin_cred_id(client, tok, hid)
                admin = await get_secret(client, tok, admin_id) if admin_id else None
                targets.append((host, admin))

        success, failure = 0, 0
        target_user = subject.get("username", "")
        secret_type = subject.get("secret_type", "password")
        secret = subject.get("secret", {})

        for h, admin_cred in targets:
            addr = h.get("ip") or h.get("ip_address") or h.get("hostname") or ""
            port = h.get("port") or h.get("ssh_port") or 22
            if not admin_cred:
                await publish_line(f"[host:{addr}] no privileged credential available to connect")
                failure += 1
                continue
            if not is_privileged(admin_cred):
                await publish_line(f"[host:{addr}] credential '{admin_cred.get('username')}' is not authorized for account operations — enable 'sudo' or mark it a 'push account'")
                failure += 1
                continue
            await publish_line(f"[host:{addr}] pushing account '{target_user}'...")

            if secret_type == "password":
                _script = f"id {target_user} &>/dev/null || useradd -m {target_user}\necho '{target_user}:{secret.get('password','')}' | chpasswd\n"
                _cmd, _stdin = sudo_exec(admin_cred, _script)
                exit_code, stdout, stderr = await _ssh_run(
                    host=addr, port=port,
                    username=admin_cred.get("username", "root"),
                    secret_type=admin_cred.get("secret_type", "password"),
                    secret=admin_cred.get("secret", {}),
                    command=_cmd,
                    stdin_data=_stdin,
                )
            elif secret_type == "ssh_key":
                from cryptography.hazmat.primitives.serialization import (
                    load_ssh_private_key, Encoding, PublicFormat
                )
                try:
                    pk = load_ssh_private_key(secret.get("private_key", "").encode(), password=None)
                    pub = pk.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode()
                except Exception as e:
                    await publish_line(f"[host:{addr}] key parse error: {e}")
                    failure += 1
                    continue

                script = (
                    f"id {target_user} &>/dev/null || useradd -m {target_user}\n"
                    f"mkdir -p /home/{target_user}/.ssh\n"
                    f"chmod 700 /home/{target_user}/.ssh\n"
                    f"grep -qF '{pub}' /home/{target_user}/.ssh/authorized_keys 2>/dev/null || echo '{pub}' >> /home/{target_user}/.ssh/authorized_keys\n"
                    f"chown -R {target_user}:{target_user} /home/{target_user}/.ssh\n"
                )
                _cmd, _stdin = sudo_exec(admin_cred, script)
                exit_code, stdout, stderr = await _ssh_run(
                    host=addr, port=port,
                    username=admin_cred.get("username", "root"),
                    secret_type=admin_cred.get("secret_type", "password"),
                    secret=admin_cred.get("secret", {}),
                    command=_cmd,
                    stdin_data=_stdin,
                )
            else:
                await publish_line(f"[host:{addr}] unsupported secret_type: {secret_type}")
                failure += 1
                continue

            if exit_code == 0:
                await publish_line(f"[host:{addr}] account push ok")
                success += 1
            else:
                await publish_line(f"[host:{addr}] account push failed (exit={exit_code}): {stderr.strip()}")
                failure += 1

        subject = None
        secret = None

        ok = failure == 0
        summary = f"account_push complete: {success} ok, {failure} failed"
        await publish_line(summary)
        return RunResult(ok=ok, output=summary, exit_code=0 if ok else 1, details={"success": success, "failure": failure})

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))
