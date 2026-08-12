"""Executor: rotate (regenerate) a credential's secret on target hosts then update the DB record."""

from __future__ import annotations

import asyncio
import secrets
from typing import Callable

from libs.pluginbase import ActionExecutor, RunRequest, RunResult

from app._ssh_exec import run_command as _ssh_run


class RotateSecretExecutor(ActionExecutor):
    name = "rotate_secret"

    def validate(self, params: dict) -> None:
        if not params.get("subject_credential_id") and not params.get("subject_cred_id"):
            raise ValueError("rotate_secret requires subject_credential_id in params")

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        from app.config import get_settings
        import httpx
        from libs.servicetoken import mint

        settings = get_settings()

        subject_cred_id = request.params.get("subject_credential_id") or request.params.get("subject_cred_id")
        policy = request.params.get("policy", {})
        host_cred_map = request.params.get("_host_credentials") or {}

        if not subject_cred_id:
            return RunResult(ok=False, output="rotate_secret requires subject_credential_id", exit_code=1)

        from app._account_ops import host_admin_cred_id, is_privileged, sudo_exec

        async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=10) as client:
            tok = mint("automation-service", "inventory-service", settings.service_jwt_secret)

            subj_resp = await client.get(f"/api/v1/internal/credentials/{subject_cred_id}/secret", headers={"X-Service-Token": tok})
            if subj_resp.status_code != 200:
                return RunResult(ok=False, output=f"subject credential lookup failed: {subj_resp.status_code}", exit_code=1)
            subject = subj_resp.json()

            # Resolve each host + its privileged connection credential (per-host override →
            # shared credential → host's push account).
            targets = []  # (host, admin_cred|None)
            for hid in request.target_host_ids:
                resp = await client.get(f"/api/v1/internal/hosts/{hid}", headers={"X-Service-Token": tok})
                if resp.status_code != 200:
                    continue
                admin_id = host_cred_map.get(hid) or request.credential_id or await host_admin_cred_id(client, tok, hid)
                admin = None
                if admin_id:
                    ar = await client.get(f"/api/v1/internal/credentials/{admin_id}/secret", headers={"X-Service-Token": tok})
                    if ar.status_code == 200:
                        admin = ar.json()
                targets.append((resp.json(), admin))

        target_user = subject.get("username", "")
        secret_type = subject.get("secret_type", "password")

        # Generate new secret in memory
        new_secret: dict
        if secret_type == "password":
            length = policy.get("length", 24)
            new_password = secrets.token_urlsafe(length)
            new_secret = {"password": new_password}
        elif secret_type == "ssh_key":
            import asyncssh
            key_type = policy.get("key_type", "ed25519")
            private_key = await asyncio.get_event_loop().run_in_executor(
                None, lambda: asyncssh.generate_private_key(key_type)
            )
            new_secret = {
                "private_key": private_key.export_private_key().decode(),
                "passphrase": "",
            }
        else:
            return RunResult(ok=False, output=f"unsupported secret_type: {secret_type}", exit_code=1)

        success, failure = 0, 0
        for h, admin_cred in targets:
            addr = h.get("ip") or h.get("ip_address") or h.get("hostname") or ""
            port = h.get("port") or h.get("ssh_port") or 22
            if not admin_cred:
                await publish_line(f"[host:{addr}] no privileged credential available to connect"); failure += 1; continue
            if not is_privileged(admin_cred):
                await publish_line(f"[host:{addr}] credential '{admin_cred.get('username')}' is not authorized for account operations — enable 'sudo' or mark it a 'push account'"); failure += 1; continue
            await publish_line(f"[host:{addr}] rotating secret for '{target_user}'...")

            if secret_type == "password":
                script = f"echo '{target_user}:{new_secret['password']}' | chpasswd\n"
            else:
                from cryptography.hazmat.primitives.serialization import (
                    load_ssh_private_key, Encoding, PublicFormat
                )
                pk = load_ssh_private_key(new_secret["private_key"].encode(), password=None)
                pub = pk.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode()
                script = (
                    f"mkdir -p /home/{target_user}/.ssh && chmod 700 /home/{target_user}/.ssh\n"
                    f"echo '{pub}' > /home/{target_user}/.ssh/authorized_keys\n"
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

            if exit_code == 0:
                await publish_line(f"[host:{addr}] rotation applied")
                success += 1
            else:
                await publish_line(f"[host:{addr}] rotation failed (exit={exit_code}): {stderr.strip()}")
                failure += 1

        targets = None

        if failure > 0:
            new_secret = None
            return RunResult(ok=False, output=f"rotation failed on {failure}/{success + failure} hosts — DB not updated", exit_code=1,
                             details={"success": success, "failure": failure})

        # Only update DB when ALL hosts succeeded
        from app.config import get_settings as _s
        import httpx
        from libs.servicetoken import mint

        settings = _s()
        async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=10) as client:
            tok = mint("automation-service", "inventory-service", settings.service_jwt_secret)
            upd_resp = await client.put(
                f"/api/v1/internal/credentials/{subject_cred_id}/secret",
                json={"secret": new_secret},
                headers={"X-Service-Token": tok},
            )
            if upd_resp.status_code not in (200, 204):
                await publish_line(f"[warn] secret rotated on hosts but DB update failed: {upd_resp.status_code}")

        new_secret = None

        summary = f"rotate_secret complete: {success} hosts updated"
        await publish_line(summary)
        return RunResult(ok=True, output=summary, exit_code=0, details={"success": success, "failure": 0})

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))
