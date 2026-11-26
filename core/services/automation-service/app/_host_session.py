"""One root SSH session to one inventory host, for the package/reboot executors.

Differs from _ssh_exec.run_command in three ways these operations need: output
is streamed, keepalives notice a dead peer, and "the connection was lost" is a
distinct result instead of an exit code.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable

import asyncssh
import httpx
from libs.pluginbase import RunRequest
from libs.servicetoken import mint

from . import _masking
from ._ssh_exec import _known_hosts
from .config import get_settings

LOST_ERRORS = (OSError, asyncssh.Error, asyncio.TimeoutError)


class HostSessionError(RuntimeError):
    """The host cannot be operated on (lookup, credential or sudo problem)."""


class HostSession:
    def __init__(self, address: str, port: int, username: str, secret_type: str, secret: dict,
                 sudo_password: str | None):
        self.address, self.port, self.username = address, port, username
        self._secret_type, self._secret, self._sudo_password = secret_type, secret, sudo_password

    @classmethod
    async def resolve(cls, host_id: str, request: RunRequest) -> "HostSession":
        s = get_settings()
        tok = {"X-Service-Token": mint("automation-service", "inventory-service", s.service_jwt_secret)}
        async with httpx.AsyncClient(base_url=s.inventory_service_url, timeout=10, headers=tok) as c:
            r = await c.get(f"/api/v1/internal/hosts/{host_id}")
            if r.status_code != 200:
                raise HostSessionError(f"host lookup failed: {r.status_code}")
            host = r.json()
            cred_id = (request.params.get("_host_credentials") or {}).get(host_id) or request.credential_id
            if not cred_id:
                r = await c.get("/api/v1/credentials", params={"host_id": host_id})
                creds = r.json() if r.status_code == 200 else []
                cred_id = creds[0]["id"] if creds else None
            if not cred_id:
                raise HostSessionError("no credential available to connect")
            cred = await cls._secret_of(c, cred_id)
            sudo_pw = None
            if cred.get("username", "root") != "root":
                sudo_id = request.params.get("sudo_credential_id")
                sudo = await cls._secret_of(c, sudo_id) if sudo_id and sudo_id != cred_id else cred
                if sudo.get("secret_type") == "password":
                    sudo_pw = (sudo.get("secret") or {}).get("password") or None
        return cls(host.get("ip") or host.get("ip_address") or "", host.get("port") or 22,
                   cred.get("username") or "root", cred.get("secret_type") or "password",
                   cred.get("secret") or {}, sudo_pw)

    @staticmethod
    async def _secret_of(c: httpx.AsyncClient, cred_id: str) -> dict:
        r = await c.get(f"/api/v1/internal/credentials/{cred_id}/secret")
        if r.status_code != 200:
            raise HostSessionError("credential lookup failed")
        cred = r.json()
        _masking.register_secret_dict(cred.get("secret"))
        return cred

    def _connect(self):
        kw: dict = {"host": self.address, "port": self.port, "username": self.username,
                    "known_hosts": _known_hosts(), "connect_timeout": 20,
                    "keepalive_interval": 15, "keepalive_count_max": 4}
        if self._secret_type == "ssh_key":
            kw["client_keys"] = [asyncssh.import_private_key(self._secret.get("private_key", ""),
                                                             passphrase=self._secret.get("passphrase"))]
        else:
            kw["password"] = self._secret.get("password", "")
        return asyncssh.connect(**kw)

    async def run(self, script: str, argv: list[str], on_line: Callable[[str], Awaitable[None]],
                  *, root: bool) -> int | None:
        """Run ``bash -s`` with the script on stdin. Returns the exit status, or
        None when the connection ended before the command did."""
        async with self._connect() as conn:
            prefix, stdin = "", script
            if root and self.username != "root":
                # Probe first: `sudo -S` with a NOPASSWD rule never reads the password
                # line, and bash would then run that line as a command.
                if (await conn.run("sudo -n true", check=False)).exit_status == 0:
                    prefix = "sudo -n -- "
                elif self._sudo_password:
                    prefix, stdin = "sudo -k -S -p '' -- ", f"{self._sudo_password}\n{script}"
                else:
                    raise HostSessionError(f"{self.username} has no passwordless sudo and no password credential "
                                           "to use with sudo; set sudo_credential_id on the system template")
            command = prefix + "bash -s -- " + " ".join(shlex.quote(a) for a in argv)
            proc = await conn.create_process(command, stderr=asyncssh.STDOUT, encoding="utf-8", errors="replace")
            try:
                proc.stdin.write(stdin)
                proc.stdin.write_eof()
            except (BrokenPipeError, ConnectionError, asyncssh.Error):
                pass  # the command ended early; its exit status says why
            async for line in proc.stdout:
                await on_line(line.rstrip("\r\n"))
            await proc.wait_closed()
            return proc.exit_status
