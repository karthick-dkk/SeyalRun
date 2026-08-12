"""Lightweight one-shot SSH command execution (no PTY, no interactive session).

Used by account_push and rotate_secret executors to apply changes on target hosts.
"""

from __future__ import annotations

import asyncio
import logging

import asyncssh

logger = logging.getLogger(__name__)
_warned_no_hostkey = False


def _known_hosts():
    """Resolve the host-key verification setting.

    Returns a known_hosts path (strict verification) when SSH_KNOWN_HOSTS_PATH is
    configured, else None (no verification) with a one-time warning. asyncssh
    treats a path as "verify against this file" and None as "accept any key".
    """
    global _warned_no_hostkey
    from app.config import get_settings

    path = (get_settings().ssh_known_hosts_path or "").strip()
    if path:
        return path
    if not _warned_no_hostkey:
        logger.warning("SSH host-key verification disabled (SSH_KNOWN_HOSTS_PATH unset) — MITM risk")
        _warned_no_hostkey = True
    return None


async def run_command(
    host: str,
    port: int,
    username: str,
    secret_type: str,
    secret: dict,
    command: str,
    stdin_data: str | None = None,
    gateway_host: str | None = None,
    gateway_port: int = 22,
    gateway_username: str | None = None,
    gateway_secret_type: str | None = None,
    gateway_secret: dict | None = None,
) -> tuple[int, str, str]:
    """Connect via SSH (with optional ProxyJump), run command, return (exit_code, stdout, stderr)."""
    known_hosts = _known_hosts()
    connect_kwargs: dict = {
        "host": host,
        "port": port,
        "username": username,
        "known_hosts": known_hosts,
    }

    if secret_type == "password":
        connect_kwargs["password"] = secret.get("password", "")
    elif secret_type == "ssh_key":
        key_data = secret.get("private_key", "")
        passphrase = secret.get("passphrase")
        connect_kwargs["client_keys"] = [asyncssh.import_private_key(key_data, passphrase=passphrase)]

    tunnel = None
    if gateway_host and gateway_secret:
        gw_kwargs: dict = {
            "host": gateway_host,
            "port": gateway_port,
            "username": gateway_username,
            "known_hosts": known_hosts,
        }
        if gateway_secret_type == "password":
            gw_kwargs["password"] = gateway_secret.get("password", "")
        elif gateway_secret_type == "ssh_key":
            key_data = gateway_secret.get("private_key", "")
            passphrase = gateway_secret.get("passphrase")
            gw_kwargs["client_keys"] = [asyncssh.import_private_key(key_data, passphrase=passphrase)]
        tunnel = await asyncssh.connect(**gw_kwargs)

    try:
        if tunnel:
            conn = await tunnel.connect_ssh(host, port=port, username=username, known_hosts=known_hosts, **{
                k: v for k, v in connect_kwargs.items() if k not in ("host", "port", "username", "known_hosts")
            })
        else:
            conn = await asyncssh.connect(**connect_kwargs)

        async with conn:
            result = await conn.run(command, input=stdin_data, check=False)
            return result.exit_status or 0, result.stdout or "", result.stderr or ""
    finally:
        if tunnel:
            tunnel.close()
