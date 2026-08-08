"""Executor: run a bash script locally or via SSH on target hosts."""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import stat
import tempfile
from typing import Callable

from libs.pluginbase import ActionExecutor, RunRequest, RunResult

from app._ssh_exec import run_command as _ssh_run


def _kill_process_group(pid: int, sig: int) -> None:
    """See ansible_playbook.py's identical helper — a script can fork its own
    children (backgrounded commands, pipelines); killing only the tracked bash
    PID would orphan them the same way an unguarded ansible-playbook kill does."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except ProcessLookupError:
        pass


def _quoted_args(raw: str) -> str:
    """Shlex-split then shlex-quote each token, so script_args can never break out of
    the `bash -s -- <args>` command line it's concatenated into (the script body itself
    goes over stdin and is never shell-parsed — only these positional args are)."""
    if not raw:
        return ""
    return " ".join(shlex.quote(tok) for tok in shlex.split(raw))


class BashScriptExecutor(ActionExecutor):
    name = "bash_script"

    def validate(self, params: dict) -> None:
        if not params.get("script_content") and not params.get("inline_script"):
            raise ValueError("bash_script requires script_content in params or job template")

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        script = request.params.get("script_content") or request.params.get("inline_script", "")
        run_local = request.params.get("run_local", False)

        if not script:
            return RunResult(ok=False, output="no script_content provided", exit_code=1)

        # run_local is an explicit opt-in (a script that intentionally targets no
        # host) — it must never be inferred from "no host was resolved/selected".
        # That used to fall through to the same local-run path, which silently
        # executed the script INSIDE this container instead of failing loudly —
        # confirmed live: a binding run with no host produced "sudo: command not
        # found" because it ran here, not on any real target.
        if run_local:
            if bool(request.params.get("_dry_run")):
                await publish_line("[dry-run] would run locally: bash -s -- " + request.params.get("script_args", ""))
                return RunResult(ok=True, output="", exit_code=0)
            return await self._run_local(script, request.params.get("script_args", ""), publish_line)

        if not request.target_host_ids:
            return RunResult(ok=False, output="no target_host_ids", exit_code=1)

        # _max_parallel is internal-only (see job_templates.py run_template's
        # stored_params) — 1 preserves the exact sequential behavior every template
        # had before this field existed, so an unset/old template is unaffected.
        max_parallel = max(1, int(request.params.get("_max_parallel") or 1))
        sem = asyncio.Semaphore(max_parallel)

        async def _bounded(host_id: str) -> bool:
            async with sem:
                ok, _out = await self._run_on_host(host_id, script, request, publish_line)
                return ok

        results = await asyncio.gather(*(_bounded(h) for h in request.target_host_ids))

        all_ok = all(results)
        return RunResult(ok=all_ok, output="", exit_code=0 if all_ok else 1)

    async def _run_local(self, script: str, script_args: str, publish_line: Callable) -> RunResult:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write(script)
            tmp = f.name
        os.chmod(tmp, stat.S_IRWXU)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", tmp, *shlex.split(script_args or ""),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            async for line in proc.stdout:
                text = line.decode().rstrip()
                await publish_line(text)
            await proc.wait()
            return RunResult(ok=(proc.returncode == 0), output="", exit_code=proc.returncode)
        except asyncio.CancelledError:
            # See ansible_playbook.py's identical guard — runner.py's timeout cancels
            # us mid-read/mid-wait; without an explicit kill this bash process (and
            # anything it forked) keeps running unmonitored after the job is already
            # marked "error".
            if proc is not None and proc.returncode is None:
                _kill_process_group(proc.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    _kill_process_group(proc.pid, signal.SIGKILL)
            raise
        finally:
            os.unlink(tmp)

    async def _run_on_host(self, host_id: str, script: str, request: RunRequest, publish_line: Callable) -> tuple[bool, str]:
        from app.config import get_settings
        import httpx

        settings = get_settings()
        await publish_line(f"[host:{host_id}] fetching host info...")

        host_cred_map = request.params.get("_host_credentials") or {}

        async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=10) as client:
            from libs.servicetoken import mint
            tok = mint("automation-service", "inventory-service", settings.service_jwt_secret)
            resp = await client.get(f"/api/v1/internal/hosts/{host_id}", headers={"X-Service-Token": tok})
            if resp.status_code != 200:
                await publish_line(f"[host:{host_id}] host lookup failed: {resp.status_code}")
                return False, ""
            host = resp.json()

            # Credential: per-host override → single shared credential → host's linked
            # credential ("default" = the push account of the server).
            cred_id = host_cred_map.get(host_id) or request.credential_id
            if not cred_id:
                rr = await client.get("/api/v1/credentials", params={"host_id": host_id},
                                      headers={"X-Service-Token": tok})
                if rr.status_code == 200 and rr.json():
                    cred_id = rr.json()[0]["id"]
            if not cred_id:
                await publish_line(f"[host:{host_id}] no credential available to connect")
                return False, ""

            cred_resp = await client.get(f"/api/v1/internal/credentials/{cred_id}/secret", headers={"X-Service-Token": tok})
            if cred_resp.status_code != 200:
                await publish_line(f"[host:{host_id}] credential lookup failed")
                return False, ""
            cred = cred_resp.json()

            # Sudo/become password: sourced from the credential vault only — never a raw
            # password in job params, which would otherwise sit in plaintext in za_job_runs
            # (persisted for history/re-run and shown in the Run Details UI). A dedicated
            # sudo_credential_id overrides; else fall back to the login credential itself
            # (matches Ansible's own become_password-defaults-to-password behavior), which
            # only works when that credential is password-type — an ssh_key login has no
            # password to hand to `sudo -S`.
            sudo_pw: str | None = None
            use_sudo = bool(request.params.get("use_sudo"))
            if use_sudo:
                sudo_cred_id = request.params.get("sudo_credential_id") or cred_id
                if sudo_cred_id == cred_id:
                    sudo_cred = cred
                else:
                    sc_resp = await client.get(f"/api/v1/internal/credentials/{sudo_cred_id}/secret",
                                               headers={"X-Service-Token": tok})
                    if sc_resp.status_code != 200:
                        await publish_line(f"[host:{host_id}] sudo credential lookup failed")
                        return False, ""
                    sudo_cred = sc_resp.json()
                if sudo_cred.get("secret_type") != "password":
                    await publish_line(f"[host:{host_id}] use_sudo requires a password-type credential "
                                       f"(the resolved credential is {sudo_cred.get('secret_type')}) — "
                                       f"set sudo_credential_id to a password credential")
                    return False, ""
                sudo_pw = sudo_cred.get("secret", {}).get("password", "")

        addr = host.get("ip") or host.get("ip_address") or host.get("hostname") or ""
        port = host.get("port") or host.get("ssh_port") or 22
        args = _quoted_args(request.params.get("script_args", ""))
        remote_cmd = f"bash -s -- {args}".strip() if args else "bash -s"
        stdin_data = script
        if use_sudo:
            # sudo -S reads the password terminated by the first newline off stdin, then
            # hands the REST of stdin to the child process — bash -s then reads the script
            # body from what's left. -p '' suppresses sudo's own "[sudo] password:" prompt
            # text so it never gets echoed into the captured output.
            remote_cmd = f"sudo -S -p '' -- {remote_cmd}"
            stdin_data = f"{sudo_pw}\n{script}"
        if bool(request.params.get("_dry_run")):
            await publish_line(f"[host:{addr}] [dry-run] would run: {remote_cmd}{' (sudo)' if use_sudo else ''}")
            return True, ""

        await publish_line(f"[host:{addr}] connecting...{' (sudo)' if use_sudo else ''}")
        exit_code, stdout, stderr = await _ssh_run(
            host=addr,
            port=port,
            username=cred.get("username", "root"),
            secret_type=cred.get("secret_type", "password"),
            secret=cred.get("secret", {}),
            command=remote_cmd,
            stdin_data=stdin_data,
        )
        for line in (stdout + stderr).splitlines():
            await publish_line(f"[host:{host_id}] {line}")
        return exit_code == 0, stdout

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))
