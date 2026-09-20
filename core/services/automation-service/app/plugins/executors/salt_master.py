"""Executor: run a Salt module/state on minions through a Salt master's salt-api.

Unlike the ansible_playbook and bash_script executors, this one never opens an SSH
connection and never touches the credential vault: the Salt master already owns the
trust relationship with its minions, so SeyalRun's job is to authenticate to
salt-api and hand it a target + function. That is why ``credential_id`` is ignored
here — there is no per-host secret to unwrap, and pretending otherwise would imply
a guarantee this path cannot make.

Connection config lives in Settings → Integration → Automation (one row in
identity-service's za_settings), not in .env, because a Salt master is something an
operator wires up after deployment. The eauth password is stored server-side and is
registered with the masking scope before use, so it can never reach a job's output
log even if salt-api echoes the request back in an error.

Targeting is deliberately explicit. ``tgt``/``tgt_type`` are passed through to
salt-api as given rather than being derived from SeyalRun's host inventory, because
a Salt minion id is not a SeyalRun host id and silently mapping between them would
let a job hit minions the operator never named. If a template does target SeyalRun
hosts, the caller sets tgt to a list of minion ids.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx

from libs.pluginbase import ActionExecutor, RunRequest, RunResult

from app import _masking

# salt-api's own vocabulary; anything else is rejected rather than forwarded, so a
# typo becomes an error here instead of an unexpectedly broad match at the master.
_TGT_TYPES = {"glob", "pcre", "list", "grain", "grain_pcre", "pillar", "nodegroup", "range", "compound", "ipcidr"}

_DEFAULT_TIMEOUT = 300.0


def normalize_tgt(tgt: Any) -> str | list[str]:
    """A list target stays a list (salt-api's `list` tgt_type expects one); anything
    else is coerced to a trimmed string. Empty is rejected by validate()."""
    if isinstance(tgt, (list, tuple)):
        return [str(t).strip() for t in tgt if str(t).strip()]
    return str(tgt or "").strip()


def build_payload(params: dict) -> dict:
    """The salt-api /run body for these params. Pure — unit-tested without a master."""
    tgt = normalize_tgt(params.get("tgt"))
    body: dict = {
        "client": params.get("client") or "local",
        "tgt": tgt,
        "fun": str(params.get("fun") or "").strip(),
        "tgt_type": params.get("tgt_type") or ("list" if isinstance(tgt, list) else "glob"),
    }
    arg = params.get("arg")
    if arg:
        body["arg"] = list(arg) if isinstance(arg, (list, tuple)) else [arg]
    kwarg = params.get("kwarg")
    if isinstance(kwarg, dict) and kwarg:
        body["kwarg"] = kwarg
    return body


def summarize(returns: Any) -> tuple[bool, str]:
    """(ok, human-readable output) from salt-api's return payload.

    Salt reports per-minion failure INSIDE a 200 response, so treating HTTP 200 as
    success would mark a job green while half the fleet errored. A minion whose
    result is explicitly False, or whose state run contains a failed state, fails
    the job.
    """
    if not isinstance(returns, list) or not returns:
        return False, "salt-api returned no result"
    first = returns[0]
    if not isinstance(first, dict):
        return False, json.dumps(returns, indent=2, default=str)
    if not first:
        return False, "no minions matched the target"

    ok = True
    lines = []
    for minion, result in sorted(first.items()):
        if result is False:
            ok = False
            lines.append(f"{minion}: FAILED")
            continue
        if isinstance(result, dict):
            # state.* returns {state_id: {"result": bool, "comment": str, ...}}
            failed = [
                sid for sid, sr in result.items()
                if isinstance(sr, dict) and sr.get("result") is False
            ]
            if failed:
                ok = False
                lines.append(f"{minion}: FAILED ({len(failed)} state(s): {', '.join(sorted(failed)[:5])})")
            else:
                lines.append(f"{minion}: ok")
            continue
        lines.append(f"{minion}: {result}")
    return ok, "\n".join(lines)


class SaltMasterExecutor(ActionExecutor):
    """Run a Salt function on minions via salt-api."""

    name = "salt_master"

    def validate(self, params: dict) -> None:
        if not str(params.get("fun") or "").strip():
            raise ValueError("salt_master requires 'fun' (e.g. state.apply, cmd.run, test.ping)")
        if not normalize_tgt(params.get("tgt")):
            raise ValueError("salt_master requires 'tgt' (minion id, glob or list)")
        tgt_type = params.get("tgt_type")
        if tgt_type and tgt_type not in _TGT_TYPES:
            raise ValueError(f"unknown tgt_type '{tgt_type}' — one of: {', '.join(sorted(_TGT_TYPES))}")

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        from app import integration_state

        cfg = await integration_state.config_for("salt")
        url = (cfg.get("url") or "").rstrip("/")
        username = cfg.get("eauth_username") or ""
        password = cfg.get("eauth_password") or ""
        eauth = cfg.get("eauth") or "pam"
        if not url or not username or not password:
            return RunResult(ok=False, exit_code=1, output=(
                "Salt master is not configured — set the URL and eauth credentials in "
                "Settings → Integration → Automation."))

        # Registered before the first request: if salt-api echoes the body back in an
        # error, the password must already be maskable by the time that line is published.
        _masking.register(password)

        body = build_payload(request.params or {})
        self.validate(request.params or {})
        body.update({"username": username, "password": password, "eauth": eauth})

        await publish_line(f"salt-api {url} :: {body['fun']} -> {body['tgt']} ({body['tgt_type']})")

        # verify stays on: a CA bundle is how a self-signed salt-api is trusted here,
        # never a disabled check.
        verify: Any = cfg.get("ca_bundle") or True
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, verify=verify) as client:
                resp = await client.post(
                    f"{url}/run",
                    json=[body],
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            return RunResult(ok=False, exit_code=1, output=f"salt-api unreachable: {exc}")

        if resp.status_code == 401:
            return RunResult(ok=False, exit_code=1,
                             output="salt-api rejected the eauth credentials (401)")
        if resp.status_code >= 400:
            return RunResult(ok=False, exit_code=1,
                             output=f"salt-api error {resp.status_code}: {_masking.mask(resp.text[:2000])}")

        try:
            payload = resp.json()
        except ValueError:
            return RunResult(ok=False, exit_code=1,
                             output=f"salt-api returned non-JSON: {_masking.mask(resp.text[:2000])}")

        ok, output = summarize((payload or {}).get("return"))
        return RunResult(ok=ok, exit_code=0 if ok else 1, output=output,
                         details={"minions": len(((payload or {}).get("return") or [{}])[0] or {})})

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))
