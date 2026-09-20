"""Executors: run Salt states and raw commands over salt-ssh — agentless, no minions.

salt-ssh is a binary this service runs, not a system it connects to: there is no
master, no minion and no eauth. What it needs is a *roster*, and SeyalRun already
holds every field one requires — address, port, username, credential, and the zone
gateway chain. So the roster is generated per run from inventory rather than kept
as a second copy of it: a materialised roster file would drift the moment a host
changed, and would leave credentials on disk permanently.

Two action types, because they have genuinely different requirements:

  salt_ssh_command   `salt-ssh -r '<cmd>'` — raw shell. Deploys no thin tarball,
                     so the target needs NOTHING installed. This is the one that
                     works on an appliance or a stripped container host.
  salt_ssh_state     applies SLS content authored on the template. Salt ships its
                     "thin" to the target, which requires python3 there.

Credentials follow the ansible_playbook executor's discipline as far as salt-ssh
allows:

  * SSH keys never touch the disk — they go into the same per-run ssh-agent, and
    the roster says ``priv: agent-forwarding``.
  * Passwords DO land in the roster file, because salt-ssh has no encrypted-roster
    equivalent of ansible-vault. The file is 0600 inside the run's own temporary
    directory and is removed with it, and every password is registered with the
    masking scope before the roster is written so it cannot surface in job output.
    This is a deliberate, accepted deviation from the no-plaintext-on-disk property
    the Ansible path holds — see R-11 in docs/compliance/risk-register.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import tempfile
from typing import Any, Callable

import yaml

from libs.pluginbase import ActionExecutor, RunRequest, RunResult

from app import _masking

# Kept in sync with ansible_playbook.py's own SSH posture: without a known_hosts
# file there is nothing to check a key against, and prompting would hang a job
# nobody is watching. Set SSH_KNOWN_HOSTS_PATH to enforce verification instead.
_SSH_OPTIONS_BASE = ["StrictHostKeyChecking=no", "UserKnownHostsFile=/dev/null"]


def proxy_jump_spec(chain: list[dict]) -> str:
    """`ssh -J` value for a gateway chain: root ancestor first, target's own zone last.

    Each hop authenticates from the per-run ssh-agent, which is why a chain
    containing a hop we have no KEY for is rejected by the caller rather than
    silently dropped — connecting directly to a host that is only supposed to be
    reachable through a bastion is a worse outcome than not running the job.
    """
    hops = []
    for hop in chain:
        host = (hop.get("host") or "").strip()
        if not host:
            continue
        user = (hop.get("username") or "").strip()
        port = int(hop.get("port") or 22)
        hops.append(f"{user}@{host}:{port}" if user else f"{host}:{port}")
    return ",".join(hops)


def build_roster(targets: list, agent_sock_path: str, thin_dir: str) -> tuple[dict, list[str]]:
    """Build a salt-ssh roster from (host, cred, sudo_pw, proxy_jump) tuples.

    Returns (roster, passwords) — the passwords are returned separately so the
    caller can register them for masking, rather than being re-extracted from the
    roster by string matching.

    Host address and username are vault- or operator-influenced strings and are
    placed as STRUCTURED values so yaml.safe_dump quotes them; nothing is
    hand-formatted into YAML text, where a newline in an address would otherwise
    append whole roster entries. The roster key is synthetic (``host_<id>``) so the
    address can never alter roster structure, and so the salt target list is stable
    even when two hosts share an address.
    """
    roster: dict[str, dict] = {}
    passwords: list[str] = []
    for idx, (host, cred, sudo_pw, proxy_jump) in enumerate(targets):
        addr = host.get("ip") or host.get("ip_address") or host.get("hostname") or ""
        try:
            port = int(host.get("port") or host.get("ssh_port") or 22)
        except (TypeError, ValueError):
            port = 22
        key = f"host_{host.get('id', idx)}"
        entry: dict[str, Any] = {
            "host": addr,
            "port": port,
            "user": (cred or {}).get("username", "root"),
            "thin_dir": thin_dir,
        }

        secret_type = (cred or {}).get("secret_type")
        if secret_type == "ssh_key":
            # The key itself is in the per-run agent; salt-ssh is told to use it
            # rather than being handed a path to a file we would have had to write.
            entry["priv"] = "agent-forwarding"
        elif secret_type == "password":
            password = (cred or {}).get("secret", {}).get("password", "")
            entry["passwd"] = password
            if password:
                passwords.append(password)

        ssh_options = list(_SSH_OPTIONS_BASE)
        if secret_type == "ssh_key":
            ssh_options.append(f"IdentityAgent={agent_sock_path}")
        if proxy_jump:
            ssh_options.append(f"ProxyJump={proxy_jump}")
        entry["ssh_options"] = ssh_options

        if sudo_pw is not None:
            # salt-ssh runs sudo without a tty by default, which prompts and hangs.
            # tty=True makes the password prompt answerable from `passwd`.
            entry["sudo"] = True
            entry["tty"] = True
            if sudo_pw and sudo_pw not in passwords:
                passwords.append(sudo_pw)
            entry.setdefault("passwd", sudo_pw)

        roster[key] = entry
    return roster, passwords


def master_config(tmpdir: str, states_dir: str) -> dict:
    """Minimal salt-ssh master config confined to the run's tempdir.

    Salt defaults every one of these paths under /etc/salt and /var/cache/salt.
    This service runs as a non-root user with a read-only root filesystem, so
    leaving them at their defaults makes salt-ssh fail on startup with a
    permission error that says nothing about the real cause.
    """
    return {
        "root_dir": tmpdir,
        "cachedir": os.path.join(tmpdir, "cache"),
        "pki_dir": os.path.join(tmpdir, "pki"),
        "log_file": os.path.join(tmpdir, "salt-ssh.log"),
        "file_roots": {"base": [states_dir]},
        "ssh_log_file": os.path.join(tmpdir, "ssh.log"),
    }


def summarize(payload: dict, raw: bool) -> tuple[bool, str]:
    """(ok, human-readable output) from salt-ssh's --out json return.

    salt-ssh exits 0 while individual targets fail, so process exit status alone
    would report a green job for a fleet that errored. Every target is inspected:
    a non-zero ``retcode``, an ``Error`` string, or any state whose ``result`` is
    False fails the whole run.
    """
    if not isinstance(payload, dict) or not payload:
        return False, "salt-ssh returned no result"

    ok = True
    lines: list[str] = []
    for target, result in sorted(payload.items()):
        if isinstance(result, dict) and result.get("retcode"):
            ok = False
        if raw:
            if isinstance(result, dict):
                stdout = (result.get("stdout") or "").rstrip()
                stderr = (result.get("stderr") or "").rstrip()
                rc = result.get("retcode", 0)
                lines.append(f"--- {target} (exit {rc})")
                if stdout:
                    lines.append(stdout)
                if stderr:
                    lines.append(f"[stderr] {stderr}")
            else:
                lines.append(f"--- {target}\n{result}")
            continue

        # state.apply: {state_id: {"result": bool, "comment": str, ...}}
        if isinstance(result, dict):
            states = {k: v for k, v in result.items() if isinstance(v, dict) and "result" in v}
            if states:
                failed = [sid for sid, sr in states.items() if sr.get("result") is False]
                changed = [sid for sid, sr in states.items() if sr.get("changes")]
                if failed:
                    ok = False
                    lines.append(f"--- {target}: FAILED ({len(failed)}/{len(states)} states)")
                    for sid in failed[:10]:
                        lines.append(f"    {sid}: {states[sid].get('comment', '')}")
                else:
                    lines.append(f"--- {target}: ok ({len(states)} states, {len(changed)} changed)")
                continue
            if "stderr" in result or "stdout" in result:
                ok = ok and not result.get("retcode")
                lines.append(f"--- {target}: {(result.get('stdout') or result.get('stderr') or '').rstrip()}")
                continue
        # Anything else (a bare error string from salt) is a failure: a target that
        # did not return a structured result did not run what it was asked to.
        ok = False
        lines.append(f"--- {target}: {result}")
    return ok, "\n".join(lines)


def salt_ssh_argv(conf_dir: str, roster_path: str, target_keys: list[str], *,
                  raw_command: str = "", state_name: str = "") -> list[str]:
    """The salt-ssh command line. Pure, so the targeting can be tested.

    Targets are an EXPLICIT list (`-L host_a,host_b`) built from the job's resolved
    host ids — never a glob from template params. A glob taken from user input is
    how a job reaches machines nobody named.
    """
    argv = [
        "salt-ssh",
        "-c", conf_dir,
        "--roster-file", roster_path,
        "--out", "json",
        "--static",              # one JSON document at the end, not a stream
        "-i",                    # accept unknown host keys (see _SSH_OPTIONS_BASE)
        "-L", ",".join(target_keys),
    ]
    if raw_command:
        argv += ["-r", raw_command]
    else:
        argv += ["state.apply", state_name]
    return argv


async def _resolve_targets(request: RunRequest, publish_line: Callable) -> list:
    """(host, cred, sudo_pw, proxy_jump) per target, mirroring the ansible executor's
    per-host credential resolution so both engines see the same inventory."""
    from app.config import get_settings
    import httpx
    from libs.servicetoken import mint

    settings = get_settings()
    host_cred_map = request.params.get("_host_credentials") or {}
    use_sudo = bool(request.params.get("use_sudo"))
    sudo_credential_id = request.params.get("sudo_credential_id")

    targets: list = []
    async with httpx.AsyncClient(base_url=settings.inventory_service_url, timeout=10) as client:
        tok = mint("automation-service", "inventory-service", settings.service_jwt_secret)

        async def _cred(cid):
            if not cid:
                return None
            r = await client.get(f"/api/v1/internal/credentials/{cid}/secret",
                                 headers={"X-Service-Token": tok})
            return r.json() if r.status_code == 200 else None

        shared_sudo_cred = await _cred(sudo_credential_id) if sudo_credential_id else None

        for hid in request.target_host_ids:
            resp = await client.get(f"/api/v1/internal/hosts/{hid}", headers={"X-Service-Token": tok})
            if resp.status_code != 200:
                continue
            host = resp.json()

            cid = host_cred_map.get(hid) or request.credential_id
            if not cid:
                rr = await client.get("/api/v1/credentials", params={"host_id": hid},
                                      headers={"X-Service-Token": tok})
                if rr.status_code == 200 and rr.json():
                    cid = rr.json()[0]["id"]
            cred = await _cred(cid)
            if not cred:
                await publish_line(f"[host:{hid}] no credential available to connect — skipping host")
                continue

            sudo_pw = None
            if use_sudo:
                sudo_cred = shared_sudo_cred if sudo_credential_id else cred
                if not sudo_cred or sudo_cred.get("secret_type") != "password":
                    await publish_line(f"[host:{hid}] use_sudo needs a password-type credential — skipping host")
                    continue
                sudo_pw = sudo_cred.get("secret", {}).get("password", "")

            # Zone gateway chain -> ProxyJump. Every hop authenticates from the
            # per-run agent, so a hop we hold no key for cannot be jumped: the host
            # is skipped rather than connected to directly, which would route around
            # the bastion the topology exists to force traffic through.
            proxy_jump = ""
            agent_hops: list[str] = []
            zone_id = host.get("zone_id")
            if zone_id:
                try:
                    cr = await client.get(f"/api/v1/internal/zones/{zone_id}/gateway-chain",
                                          headers={"X-Service-Token": tok})
                    chain = (cr.json() or {}).get("chain") or [] if cr.status_code == 200 else []
                except httpx.HTTPError:
                    chain = []
                chain = [h for h in chain
                         if (h.get("host") or "").strip().lower() != (host.get("ip") or "").strip().lower()]
                if chain:
                    unusable = []
                    for hop in chain:
                        hop_cred = await _cred(hop.get("credential_id"))
                        if not hop_cred or hop_cred.get("secret_type") != "ssh_key":
                            unusable.append(hop.get("host") or "?")
                        else:
                            agent_hops.append(hop_cred.get("secret", {}).get("private_key", ""))
                    if unusable:
                        await publish_line(
                            f"[host:{hid}] gateway(s) {', '.join(unusable)} have no SSH-key credential — "
                            f"salt-ssh jumps with the ssh-agent, so this host is skipped rather than "
                            f"connected to directly")
                        continue
                    proxy_jump = proxy_jump_spec(chain)
            targets.append((host, cred, sudo_pw, proxy_jump, agent_hops))
    return targets


async def _run_salt_ssh(targets: list, publish_line: Callable, *,
                        raw_command: str = "", state_content: str = "") -> RunResult:
    """Shared dispatch: per-run ssh-agent + tempdir roster/config, stream output."""
    from app.plugins.executors.ansible_playbook import _agent_add_key, _start_ssh_agent

    with tempfile.TemporaryDirectory() as tmpdir:
        states_dir = os.path.join(tmpdir, "states")
        os.makedirs(states_dir, exist_ok=True)
        thin_dir = os.path.join("/tmp", f".seyalrun-salt-{os.path.basename(tmpdir)}")

        agent_proc, agent_sock = await _start_ssh_agent(tmpdir)
        try:
            flat_targets = []
            for host, cred, sudo_pw, proxy_jump, agent_hops in targets:
                if (cred or {}).get("secret_type") == "ssh_key":
                    await _agent_add_key(agent_sock, cred.get("secret", {}).get("private_key", ""))
                for hop_key in agent_hops:
                    await _agent_add_key(agent_sock, hop_key)
                flat_targets.append((host, cred, sudo_pw, proxy_jump))

            roster, passwords = build_roster(flat_targets, agent_sock, thin_dir)
            # Registered BEFORE the roster is written and before salt runs: if salt
            # echoes the roster back in an error, the value must already be maskable.
            for pw in passwords:
                _masking.register(pw)

            roster_path = os.path.join(tmpdir, "roster")
            fd = os.open(roster_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                yaml.safe_dump(roster, fh, default_flow_style=False)

            conf_dir = os.path.join(tmpdir, "conf")
            os.makedirs(conf_dir, exist_ok=True)
            with open(os.path.join(conf_dir, "master"), "w") as fh:
                yaml.safe_dump(master_config(tmpdir, states_dir), fh, default_flow_style=False)

            state_name = ""
            if state_content:
                state_name = "seyalrun_job"
                with open(os.path.join(states_dir, f"{state_name}.sls"), "w") as fh:
                    fh.write(state_content)

            argv = salt_ssh_argv(conf_dir, roster_path, sorted(roster.keys()),
                                 raw_command=raw_command, state_name=state_name)
            await publish_line(f"$ salt-ssh -L {','.join(sorted(roster.keys()))} "
                               f"{'-r ' + shlex.quote(raw_command) if raw_command else 'state.apply ' + state_name}")

            env = dict(os.environ, SSH_AUTH_SOCK=agent_sock)
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env, cwd=tmpdir,
            )
            out_bytes, _ = await proc.communicate()
            text = out_bytes.decode(errors="replace")

            # --out json --static prints one JSON document; anything salt wrote to
            # stderr lands in front of it, so the payload is located rather than
            # assumed to start at byte 0.
            payload = None
            brace = text.find("{")
            if brace >= 0:
                try:
                    payload = json.loads(text[brace:])
                except ValueError:
                    payload = None
            if payload is None:
                masked = _masking.mask(text[-4000:])
                return RunResult(ok=False, exit_code=proc.returncode or 1,
                                 output=f"salt-ssh produced no JSON result:\n{masked}")

            ok, summary = summarize(payload, raw=bool(raw_command))
            return RunResult(ok=ok, exit_code=0 if ok else (proc.returncode or 1),
                             output=_masking.mask(summary), details={"targets": len(roster)})
        finally:
            if agent_proc is not None:
                agent_proc.terminate()


class SaltSshCommandExecutor(ActionExecutor):
    """Run a raw shell command on hosts over salt-ssh (no Python needed on target)."""

    name = "salt_ssh_command"

    def validate(self, params: dict) -> None:
        if not str(params.get("command") or params.get("script_content") or "").strip():
            raise ValueError("salt_ssh_command requires 'command' in params")

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        if not request.target_host_ids:
            return RunResult(ok=False, output="no target_host_ids", exit_code=1)
        command = str(request.params.get("command") or request.params.get("script_content") or "").strip()
        if not command:
            return RunResult(ok=False, output="no command", exit_code=1)
        targets = await _resolve_targets(request, publish_line)
        if not targets:
            return RunResult(ok=False, output="could not resolve any target hosts", exit_code=1)
        return await _run_salt_ssh(targets, publish_line, raw_command=command)

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))


class SaltSshStateExecutor(ActionExecutor):
    """Apply Salt state (SLS) content to hosts over salt-ssh."""

    name = "salt_ssh_state"

    def validate(self, params: dict) -> None:
        if not str(params.get("script_content") or "").strip():
            raise ValueError("salt_ssh_state requires SLS content in script_content")

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        if not request.target_host_ids:
            return RunResult(ok=False, output="no target_host_ids", exit_code=1)
        sls = str(request.params.get("script_content") or "")
        if not sls.strip():
            return RunResult(ok=False, output="no SLS content", exit_code=1)
        targets = await _resolve_targets(request, publish_line)
        if not targets:
            return RunResult(ok=False, output="could not resolve any target hosts", exit_code=1)
        return await _run_salt_ssh(targets, publish_line, state_content=sls)

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))
