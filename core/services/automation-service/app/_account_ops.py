"""Shared helpers for account-management executors (create/push, rotate, disable, remove).

The privileged SSH connection credential is resolved per host: the template's
credential_id if set, otherwise the first credential linked to that host in inventory —
so the default account-operation templates work across hosts without a fixed admin login.
"""

from __future__ import annotations

import re
import shlex
from typing import Callable

import httpx

from app._ssh_exec import run_command as _ssh_run


def _inv_token():
    from app.config import get_settings
    from libs.servicetoken import mint
    s = get_settings()
    return s.inventory_service_url, mint("automation-service", "inventory-service", s.service_jwt_secret)


async def get_secret(client: httpx.AsyncClient, token: str, cred_id: str) -> dict | None:
    r = await client.get(f"/api/v1/internal/credentials/{cred_id}/secret", headers={"X-Service-Token": token})
    return r.json() if r.status_code == 200 else None


async def host_admin_cred_id(client: httpx.AsyncClient, token: str, host_id: str) -> str | None:
    """Privileged login linked to the host ("default = the host's push account"): prefer a
    credential flagged as a push account, then any sudo-capable one, then the first linked."""
    r = await client.get(f"/api/v1/credentials", params={"host_id": host_id}, headers={"X-Service-Token": token})
    if r.status_code != 200 or not r.json():
        return None
    creds = r.json()
    for c in creds:
        if c.get("is_push_account"):
            return c["id"]
    for c in creds:
        if c.get("is_sudo"):
            return c["id"]
    return creds[0]["id"]


def is_privileged(cred: dict) -> bool:
    """Account operations are only allowed with a privileged connection credential: one
    flagged sudo-capable or as a push account (root logins are inherently privileged)."""
    return bool(cred.get("is_sudo") or cred.get("is_push_account") or cred.get("username") == "root")


def conn_command(cred: dict) -> str:
    """Shell entrypoint for the account snippet — escalates via `sudo -n` when the
    connection login is sudo-flagged and isn't already root."""
    if cred.get("is_sudo") and cred.get("username") != "root":
        return "sudo -n bash -s"
    return "bash -s"


def sudo_exec(cred: dict, script: str) -> tuple[str, str]:
    """Return (command, stdin_data) that runs ``script`` as root over a single SSH exec.

    - root login (or non-sudo cred): run the script directly with ``bash -s``.
    - sudo + password credential: ``sudo -S -p '' bash -s`` — the password is supplied as
      the first stdin line (sudo consumes it, bash reads the rest as the script), so a
      password-protected sudo works without host-side NOPASSWD.
    - sudo + key credential: ``sudo -n bash -s`` (no password available — needs NOPASSWD).

    The password travels only over the already-encrypted SSH channel and is never logged.
    """
    if cred.get("username") == "root" or not cred.get("is_sudo"):
        return "bash -s", script
    if cred.get("secret_type") == "password":
        pw = (cred.get("secret") or {}).get("password", "")
        return "sudo -S -p '' bash -s", f"{pw}\n{script}"
    return "sudo -n bash -s", script


async def get_host(client: httpx.AsyncClient, token: str, host_id: str) -> dict | None:
    r = await client.get(f"/api/v1/internal/hosts/{host_id}", headers={"X-Service-Token": token})
    return r.json() if r.status_code == 200 else None


class UnsafeAccountValue(ValueError):
    """Raised when a value cannot be safely placed in a privileged shell command."""


# POSIX-portable account names. Deliberately narrow: these strings end up in
# commands that run as root on managed hosts, and there is no legitimate account
# name that needs a quote, a semicolon or a newline in it.
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}\$?$")


def validate_username(username: str) -> str:
    """Reject an account name that has no business in a root shell command.

    Belt and braces with sh_quote() below. Quoting alone is sufficient against
    injection, but a name like `; rm -rf /` would then be *correctly* passed to
    useradd as a literal account name — technically safe, obviously wrong, and
    the kind of thing that should fail loudly at the boundary rather than create
    an absurd account on fifty hosts.
    """
    if not _USERNAME_RE.match(username or ""):
        raise UnsafeAccountValue(
            f"unsafe account name {username!r} — must match {_USERNAME_RE.pattern}"
        )
    return username


def sh_quote(value: str) -> str:
    """Quote a value for safe interpolation into a POSIX shell command.

    This exists because every account executor used to build its script with an
    f-string and hand-written single quotes:

        echo '{user}:{password}' | chpasswd

    A password containing a single quote closes that quote and everything after
    it becomes a NEW COMMAND, running as root on every target host — an
    escalation from "may create a credential" to "arbitrary root across the
    fleet". Credential secrets are admin-supplied and unvalidated, so this was
    reachable, not theoretical.
    """
    return shlex.quote(value or "")


def chpasswd_script(username: str, password: str) -> str:
    """`user:password | chpasswd`, with the pair quoted as a single argument.

    printf rather than echo: echo's handling of backslashes and leading `-` is
    shell-dependent, and a password is exactly the kind of string that contains
    both. printf '%s' is defined behaviour everywhere.
    """
    validate_username(username)
    return f"printf '%s\\n' {sh_quote(f'{username}:{password}')} | chpasswd\n"


# op -> shell snippet builder (Linux). Every interpolation is quoted.
def _op_snippet(op: str, username: str) -> str:
    u = sh_quote(validate_username(username))
    if op == "disable":
        return (
            f"if id {u} &>/dev/null; then usermod -L {u}; usermod -e 1 {u} 2>/dev/null || true; "
            f"echo \"[ok] $(printf '%s' {u}) disabled\"; else echo \"[skip] no such user $(printf '%s' {u})\"; fi"
        )
    if op == "remove":
        return (
            f"if id {u} &>/dev/null; then userdel -r {u} 2>/dev/null && echo \"[ok] $(printf '%s' {u}) removed\" "
            f"|| (userdel {u} && echo \"[ok] $(printf '%s' {u}) removed (home kept)\"); "
            f"else echo \"[skip] no such user $(printf '%s' {u})\"; fi"
        )
    raise UnsafeAccountValue(f"unknown account op {op!r}")


async def run_account_op(request, publish_line: Callable, op: str):
    """Connect to each target host with a privileged credential and run an idempotent
    user-management command for the subject account."""
    from libs.pluginbase import RunResult

    base_url, token = _inv_token()
    subject_cred_id = request.params.get("subject_credential_id") or request.params.get("subject_cred_id")
    if not subject_cred_id:
        return RunResult(ok=False, output="subject_credential_id is required", exit_code=1)

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        subject = await get_secret(client, token, subject_cred_id)
        if not subject:
            return RunResult(ok=False, output="subject credential lookup failed", exit_code=1)
        target_user = subject.get("username", "")
        if not target_user:
            return RunResult(ok=False, output="subject credential has no username", exit_code=1)

        snippet = _op_snippet(op, target_user)
        # Per-host credential overrides, then a single shared credential, then the host's
        # own linked credential ("default" = the push account of the server).
        host_cred_map = request.params.get("_host_credentials") or {}
        success, failure = 0, 0
        for hid in request.target_host_ids:
            host = await get_host(client, token, hid)
            if not host:
                await publish_line(f"[host:{hid}] not found"); failure += 1; continue
            addr = host.get("ip") or host.get("ip_address") or host.get("hostname") or ""
            port = host.get("port") or host.get("ssh_port") or 22

            admin_id = host_cred_map.get(hid) or request.credential_id or await host_admin_cred_id(client, token, hid)
            admin = await get_secret(client, token, admin_id) if admin_id else None
            if not admin:
                await publish_line(f"[host:{addr}] no privileged credential available to connect"); failure += 1; continue
            if not is_privileged(admin):
                await publish_line(f"[host:{addr}] credential '{admin.get('username')}' is not authorized for account operations — enable 'sudo' or mark it a 'push account'"); failure += 1; continue

            await publish_line(f"[host:{addr}] {op} account '{target_user}'...")
            cmd, stdin = sudo_exec(admin, snippet + "\n")
            try:
                code, out, err = await _ssh_run(
                    host=addr, port=port,
                    username=admin.get("username", "root"),
                    secret_type=admin.get("secret_type", "password"),
                    secret=admin.get("secret", {}),
                    command=cmd, stdin_data=stdin,
                )
            except Exception as exc:  # noqa: BLE001
                await publish_line(f"[host:{addr}] connection error: {exc}"); failure += 1; continue
            for line in (out or "").splitlines():
                await publish_line(f"[host:{addr}] {line}")
            if code == 0:
                success += 1
            else:
                await publish_line(f"[host:{addr}] failed (exit={code}): {(err or '').strip()}"); failure += 1

    ok = failure == 0
    summary = f"{op}: {success} ok, {failure} failed"
    await publish_line(summary)
    return RunResult(ok=ok, output=summary, exit_code=0 if ok else 1, details={"success": success, "failure": failure})
