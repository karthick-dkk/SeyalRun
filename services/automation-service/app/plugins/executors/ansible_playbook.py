"""Executor: run an Ansible playbook against target hosts."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import signal
import stat
import tempfile
import textwrap
from typing import Callable

import yaml

from libs.pluginbase import ActionExecutor, RunRequest, RunResult

_TASK_RE = re.compile(r"^TASK \[(.*)\] \*+$")
_RESULT_RE = re.compile(r"^(changed|ok|failed|fatal|skipping): \[([^\]\s]+)")


class _DiffCollector:
    """Parses --check --diff output into {host: [{"task": ..., "diff": ...}]},
    for PCI DSS Phase D's change-control diff_summary. Ansible's default
    callback prints a task's diff block (the "--- before"/"+++ after"/"@@"
    unified-diff text) immediately before the per-host result line
    ("changed: [host]") for that same task — so a diff block is buffered
    until the following result line reveals which host it belongs to."""

    def __init__(self) -> None:
        self._current_task = ""
        self._buffer: list[str] = []
        self._in_diff = False
        self.by_host: dict[str, list[dict]] = {}

    def feed(self, line: str) -> None:
        task_match = _TASK_RE.match(line)
        if task_match:
            self._current_task = task_match.group(1)
            self._buffer = []
            self._in_diff = False
            return
        if line.startswith("---") and "before" in line:
            self._in_diff = True
            self._buffer = [line]
            return
        if self._in_diff:
            result_match = _RESULT_RE.match(line)
            if result_match:
                host = result_match.group(2)
                diff_text = "\n".join(self._buffer).strip()
                if diff_text:
                    self.by_host.setdefault(host, []).append({"task": self._current_task, "diff": diff_text})
                self._buffer = []
                self._in_diff = False
                return
            self._buffer.append(line)


def _kill_process_group(pid: int, sig: int) -> None:
    """Signal the whole process group (see start_new_session=True below) rather
    than just the tracked PID — ProcessLookupError means the group is already
    gone (process exited between our check and the signal), safe to ignore."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except ProcessLookupError:
        pass


def build_inventory(hosts: list, agent_sock_path: str) -> tuple[dict, dict[str, dict], list[tuple[str, str]]]:
    """Build a YAML-safe Ansible inventory from (host, cred, sudo_pw) triples.

    Host address / username are attacker-or-vault-influenced strings. They are
    placed as STRUCTURED values so yaml.safe_dump escapes them — never hand-formatted
    into INI text, where a value with a space would inject extra inventory vars (e.g.
    ansible_ssh_common_args='-o ProxyCommand=...') and a newline would inject whole host
    entries. A synthetic host key (host_<id>) decouples the inventory name from the
    connection address so the address can never alter inventory structure.

    PCI DSS Phase C: neither the login password, the become password, nor the raw SSH
    private key is EVER placed in this returned inventory dict (which the caller writes
    as plaintext inventory.yml) — that was the plaintext-on-disk-during-execution gap.
    Returns (inventory, vault_vars_by_host, agent_keys):
      - inventory: connection vars only (host/port/user/agent-socket) — safe to write
        as plaintext YAML, nothing here is a credential.
      - vault_vars_by_host: {host_key: {ansible_ssh_pass?, ansible_become_password?}} —
        the caller writes each as an ansible-vault-ENCRYPTED host_vars/<host_key>/vault.yml,
        decrypted only by ansible-playbook itself via --vault-password-file.
      - agent_keys: [(host_key, private_key_pem), ...] — the caller loads these into a
        per-run ssh-agent instead of ever writing a key file to disk.
    """
    inv_hosts: dict = {}
    vault_vars: dict[str, dict] = {}
    agent_keys: list[tuple[str, str]] = []
    for idx, (h, cred, sudo_pw) in enumerate(hosts):
        addr = h.get("ip") or h.get("ip_address") or h.get("hostname") or ""
        try:
            port = int(h.get("port") or h.get("ssh_port") or 22)
        except (TypeError, ValueError):
            port = 22
        host_key = f"host_{h.get('id', idx)}"
        hvars: dict = {"ansible_host": addr, "ansible_port": port}
        hvault: dict = {}
        if cred:
            hvars["ansible_user"] = cred.get("username", "root")
            if cred.get("secret_type") == "password":
                hvault["ansible_ssh_pass"] = cred["secret"].get("password", "")
            elif cred.get("secret_type") == "ssh_key":
                agent_keys.append((host_key, cred["secret"].get("private_key", "")))
                # Per-host override of --ssh-common-args must carry the same
                # StrictHostKeyChecking/UserKnownHostsFile bits the CLI flag sets
                # globally — an inventory-level ansible_ssh_common_args REPLACES,
                # not appends to, the CLI value.
                hvars["ansible_ssh_common_args"] = (
                    "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
                    f"-o IdentityAgent={agent_sock_path}"
                )
        if sudo_pw is not None:
            hvars["ansible_become"] = True
            hvars["ansible_become_method"] = "sudo"
            hvault["ansible_become_password"] = sudo_pw
        inv_hosts[host_key] = hvars
        if hvault:
            vault_vars[host_key] = hvault
    return {"all": {"children": {"targets": {"hosts": inv_hosts}}}}, vault_vars, agent_keys


async def _start_ssh_agent(tmpdir: str) -> tuple[asyncio.subprocess.Process | None, str]:
    """Per-run ssh-agent, killed alongside the playbook run — private keys are
    loaded into it (ssh-add -, from stdin) and never written to a key file."""
    sock_path = os.path.join(tmpdir, "agent.sock")
    proc = await asyncio.create_subprocess_exec(
        "ssh-agent", "-D", "-a", sock_path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(50):  # ~5s cap; the socket typically appears in well under 100ms
        if os.path.exists(sock_path):
            return proc, sock_path
        await asyncio.sleep(0.1)
    _kill_process_group(proc.pid, signal.SIGTERM)
    return None, sock_path


async def _agent_add_key(sock_path: str, private_key: str) -> bool:
    if not private_key:
        return False
    proc = await asyncio.create_subprocess_exec(
        "ssh-add", "-",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "SSH_AUTH_SOCK": sock_path},
    )
    key_bytes = private_key.encode() if private_key.endswith("\n") else (private_key + "\n").encode()
    await proc.communicate(key_bytes)
    return proc.returncode == 0


async def _write_encrypted_vault_yaml(path: str, data: dict, password: str, tmpdir: str) -> None:
    """Writes plaintext then encrypts it in-place via the `ansible-vault` CLI as its
    own subprocess — NOT the `ansible.parsing.vault` library in-process. Importing
    that module triggers ansible.constants' config init against the REAL process
    HOME (read-only in this container) the first time it's ever imported in a given
    process, crashing; even if worked around, mutating os.environ["HOME"] to fix it
    would race against any OTHER ansible job running concurrently in this same
    process (runner.py dispatches jobs as concurrent asyncio tasks, not serially).
    A separate subprocess with its own isolated env sidesteps both problems.

    There's a brief window here where `path` holds plaintext before the encrypt
    call completes (milliseconds) — smaller than this replaces (the whole
    ansible-playbook run duration previously had ansible_ssh_pass/become_password
    sitting in inventory.yml as plaintext for its entire lifetime).
    """
    plaintext = yaml.safe_dump(data, default_flow_style=False)
    with open(path, "w") as f:
        f.write(plaintext)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    read_fd, write_fd = os.pipe()
    os.write(write_fd, password.encode() + b"\n")
    os.close(write_fd)
    os.set_inheritable(read_fd, True)
    ansible_home = os.path.join(tmpdir, ".ansible-vault-encrypt")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ansible-vault", "encrypt", path, "--vault-password-file", f"/dev/fd/{read_fd}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={
                **os.environ, "HOME": tmpdir,
                "ANSIBLE_HOME": ansible_home, "ANSIBLE_LOCAL_TEMP": os.path.join(ansible_home, "tmp"),
            },
            pass_fds=(read_fd,),
        )
    finally:
        os.close(read_fd)
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ansible-vault encrypt failed: {out.decode(errors='replace')}")


class AnsiblePlaybookExecutor(ActionExecutor):
    name = "ansible_playbook"

    def validate(self, params: dict) -> None:
        # playbook_path was never actually read by execute() below — only
        # script_content ever ran — so it's not accepted as an alternative here.
        if not params.get("script_content"):
            raise ValueError("ansible_playbook requires script_content in params")

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        from app.config import get_settings
        import httpx
        from libs.servicetoken import mint

        settings = get_settings()

        if not request.target_host_ids:
            return RunResult(ok=False, output="no target_host_ids", exit_code=1)

        # Per-host credential resolution: per-host override → single shared credential →
        # the host's own linked credential ("default" = the push account of the server).
        host_cred_map = request.params.get("_host_credentials") or {}

        async def _resolve_cred(client, tok, cid):
            if not cid:
                return None
            cr = await client.get(f"/api/v1/internal/credentials/{cid}/secret",
                                  headers={"X-Service-Token": tok})
            return cr.json() if cr.status_code == 200 else None

        async def _resolve_login_cred(client, tok, hid):
            cid = host_cred_map.get(hid) or request.credential_id
            if not cid:
                rr = await client.get("/api/v1/credentials", params={"host_id": hid},
                                      headers={"X-Service-Token": tok})
                if rr.status_code == 200 and rr.json():
                    cid = rr.json()[0]["id"]
            return cid, await _resolve_cred(client, tok, cid)

        # Become/sudo password: sourced from the credential vault only, same discipline as
        # bash_script's sudo support — never a raw password sitting in job params, which
        # persist to za_job_runs and render in the Run Details UI. sudo_credential_id
        # overrides; else falls back to each host's own login credential (matching
        # Ansible's own ansible_become_password-defaults-to-ansible_password behavior),
        # which only works when that credential is password-type.
        use_sudo = bool(request.params.get("use_sudo"))
        sudo_credential_id = request.params.get("sudo_credential_id")

        hosts = []  # list of (host_dict, cred_dict|None, sudo_password|None)
        async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=10) as client:
            tok = mint("automation-service", "inventory-service", settings.service_jwt_secret)
            shared_sudo_cred = await _resolve_cred(client, tok, sudo_credential_id) if sudo_credential_id else None
            for hid in request.target_host_ids:
                resp = await client.get(f"/api/v1/internal/hosts/{hid}", headers={"X-Service-Token": tok})
                if resp.status_code != 200:
                    continue
                login_cid, cred = await _resolve_login_cred(client, tok, hid)
                if not cred:
                    await publish_line(f"[host:{hid}] no credential available to connect — skipping host")
                    continue
                sudo_pw = None
                if use_sudo:
                    sudo_cred = shared_sudo_cred if sudo_credential_id else cred
                    if not sudo_cred or sudo_cred.get("secret_type") != "password":
                        await publish_line(f"[host:{hid}] use_sudo requires a password-type credential "
                                           f"— set sudo_credential_id to a password credential, skipping host")
                        continue
                    sudo_pw = sudo_cred.get("secret", {}).get("password", "")
                hosts.append((resp.json(), cred, sudo_pw))

        if not hosts:
            return RunResult(ok=False, output="could not resolve any target hosts", exit_code=1)

        playbook_content = request.params.get("script_content", "")
        if not playbook_content:
            await publish_line("[error] no playbook content")
            return RunResult(ok=False, output="no playbook content", exit_code=1)

        extra_vars = {k: v for k, v in request.params.items()
                      if k not in ("script_content", "run_local") and not k.startswith("_")}
        dry_run = bool(request.params.get("_dry_run"))
        forks = request.params.get("_forks")
        return await run_ansible_playbook(hosts, playbook_content, publish_line, extra_vars, dry_run=dry_run, forks=forks)

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))


async def run_ansible_playbook(
    hosts: list,
    playbook_content: str,
    publish_line: Callable,
    extra_vars: dict | None = None,
    dry_run: bool = False,
    forks: int | None = None,
) -> RunResult:
    """Shared subprocess dispatch — writes a YAML-safe inventory + the given
    playbook into a tempdir and runs `ansible-playbook` against it, streaming
    output via publish_line. Extracted from AnsiblePlaybookExecutor.execute()
    (pure move, same behavior) so the standalone connectivity-test endpoint
    (app/api/test_connection.py) can reuse this exact dispatch — tempdir
    scoping, read-only-rootfs-safe env vars, StrictHostKeyChecking=no — rather
    than re-implementing it a second time.

    PCI DSS Phase C: no login password, become password, or SSH private key is
    ever written to disk in plaintext during a run. Passwords go into a per-host
    ansible-vault-encrypted host_vars/<host>/vault.yml, whose own vault password
    is generated per-run and handed to ansible-playbook through an anonymous
    pipe (--vault-password-file /dev/fd/N) — never a file, never an argv string
    a `ps` on the host could read. SSH keys are loaded into a per-run ssh-agent
    (ssh-add -, from stdin) instead of a key_<id> file.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        needs_agent = any(cred and cred.get("secret_type") == "ssh_key" for _, cred, _ in hosts)
        agent_proc = None
        agent_sock = os.path.join(tmpdir, "agent.sock")
        if needs_agent:
            agent_proc, agent_sock = await _start_ssh_agent(tmpdir)
            if agent_proc is None:
                await publish_line("[warn] ssh-agent failed to start — key-based hosts may fail to connect")

        inventory, vault_vars, agent_keys = build_inventory(hosts, agent_sock)

        if agent_proc:
            for _host_key, priv_key in agent_keys:
                await _agent_add_key(agent_sock, priv_key)

        inv_path = os.path.join(tmpdir, "inventory.yml")
        with open(inv_path, "w") as f:
            yaml.safe_dump(inventory, f, default_flow_style=False, allow_unicode=True)

        vault_password = secrets.token_hex(32)
        for host_key, hvault in vault_vars.items():
            hv_dir = os.path.join(tmpdir, "host_vars", host_key)
            os.makedirs(hv_dir, exist_ok=True)
            await _write_encrypted_vault_yaml(os.path.join(hv_dir, "vault.yml"), hvault, vault_password, tmpdir)

        pb_path = os.path.join(tmpdir, "playbook.yml")
        with open(pb_path, "w") as f:
            f.write(playbook_content)

        cmd = [
            "ansible-playbook",
            "-i", inv_path,
            pb_path,
            # UserKnownHostsFile=/dev/null avoids writing ~/.ssh on the read-only
            # container FS. No BatchMode — it would disable sshpass password auth.
            "--ssh-common-args=-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
        ]
        read_fd: int | None = None
        if vault_vars:
            read_fd, write_fd = os.pipe()
            os.write(write_fd, vault_password.encode() + b"\n")
            os.close(write_fd)
            os.set_inheritable(read_fd, True)
            cmd += ["--vault-password-file", f"/dev/fd/{read_fd}"]
        if extra_vars:
            cmd += ["--extra-vars", json.dumps(extra_vars)]
        if dry_run:
            # --check simulates without applying changes; --diff shows what would
            # change for modules that support it. Requested as a per-run safety
            # option (RunPayload.dry_run), never a template default, so any caller
            # can always ask for the safer path regardless of the template's own
            # allowed_param_keys.
            cmd += ["--check", "--diff"]
        if forks:
            # Ansible's own default fork count is 5 — null/unset leaves that
            # untouched; only an explicit template value overrides it.
            cmd += ["--forks", str(forks)]

        await publish_line(f"[ansible] running playbook against {len(hosts)} host(s)...{' (DRY RUN — no changes will be applied)' if dry_run else ''}")
        # PYTHONUNBUFFERED=1 + ANSIBLE_STDOUT_CALLBACK=default forces line-buffered
        # stdout so each output line is published to Redis immediately rather than
        # being held until the subprocess exits.
        # The container runs read_only with only /tmp + /playbooks writable, so point
        # ansible's home/temp dirs at /tmp (else it fails creating ~/.ansible/tmp).
        ansible_home = os.path.join(tmpdir, ".ansible")
        proc_env = {
            **os.environ,
            "HOME": tmpdir,
            "PYTHONUNBUFFERED": "1",
            "ANSIBLE_FORCE_COLOR": "0",
            "ANSIBLE_STDOUT_CALLBACK": "default",
            "ANSIBLE_HOST_KEY_CHECKING": "False",
            "ANSIBLE_HOME": ansible_home,
            "ANSIBLE_LOCAL_TEMP": os.path.join(ansible_home, "tmp"),
            "ANSIBLE_PERSISTENT_CONTROL_PATH_DIR": os.path.join(ansible_home, "pc"),
        }
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=tmpdir,
                env=proc_env,
                # New session/process group so ansible-playbook's own forked children
                # (ssh, sshpass, and especially the ControlPersist mux connection it
                # deliberately keeps alive for reuse) share one killable group. Without
                # this, terminate()/kill() below only reach the ansible-playbook PID
                # itself — confirmed live: SIGTERM'd ansible-playbook exits cleanly in
                # ~20ms, but its orphaned ssh children (and the remote command they're
                # still attached to) keep running well past the job's own timeout.
                start_new_session=True,
                pass_fds=(read_fd,) if read_fd is not None else (),
            )
        finally:
            if read_fd is not None:
                os.close(read_fd)  # child holds its own dup after exec; drop ours either way
        diff_collector = _DiffCollector() if dry_run else None
        try:
            async for line in proc.stdout:
                text = line.decode().rstrip()
                await publish_line(text)
                if diff_collector is not None:
                    diff_collector.feed(text)
            await proc.wait()
        except asyncio.CancelledError:
            # runner.py's asyncio.wait_for(...) timeout cancels us right here (mid
            # stdout-read or mid-wait) — without an explicit kill, ansible-playbook
            # (and everything it forked over SSH) keeps running unmonitored after the
            # job is already marked "error" in the DB. SIGTERM the whole process
            # group first (lets it clean up), SIGKILL the group if it's still alive
            # a moment later.
            _kill_process_group(proc.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                _kill_process_group(proc.pid, signal.SIGKILL)
            raise
        finally:
            if agent_proc:
                _kill_process_group(agent_proc.pid, signal.SIGTERM)
        ok = proc.returncode == 0
        details = {"diff_summary": diff_collector.by_host} if diff_collector is not None and diff_collector.by_host else {}
        return RunResult(ok=ok, output="", exit_code=proc.returncode, details=details)
