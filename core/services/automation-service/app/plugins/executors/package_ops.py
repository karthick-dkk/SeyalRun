"""Executor: one package operation on one host (3.0 M3). See host_op_launcher.sh."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from libs.pluginbase import ActionExecutor, RunRequest, RunResult

from app._host_ops import HOST_OP_PARAM, OP_TOKEN_PARAM, PACKAGE_ACTION, HostOpError, argv_for, check_token
from app._host_session import LOST_ERRORS, HostSession, HostSessionError

_DIR = Path(__file__).parent
LAUNCHER = (_DIR / "host_op_launcher.sh").read_text()
PACKAGE_WORKER = (_DIR / "package_ops.sh").read_text()
RECONNECT_DELAY = 10
MAX_RECONNECTS = 60


def launcher_script(worker: str) -> str:
    return LAUNCHER.replace("@@WORKER@@", worker)


class _Tail:
    """Counts the worker's log lines so a reconnect continues where it stopped."""

    def __init__(self, publish: Callable, label: str):
        self.publish, self.label, self.seen, self.lines = publish, label, 0, []
        self.in_log = False

    async def __call__(self, line: str) -> None:
        # sudo, locale and login-shell chatter comes before the marker: counting it as
        # log lines would skip real ones after a reconnect.
        if not self.in_log and line == "SEYALRUN-LOG":
            self.in_log = True
            return
        if self.in_log:
            self.seen += 1
            self.lines.append(line)
        await self.publish(f"[host:{self.label}] {line}")


async def run_detached(session: HostSession, worker: str, token: str, argv: list[str], label: str,
                       publish: Callable) -> tuple[int, list[str]]:
    """Launch (or re-attach to) the operation and follow it to its exit code."""
    tail, script = _Tail(publish, label), launcher_script(worker)
    for attempt in range(MAX_RECONNECTS + 1):
        tail.in_log = False
        try:
            rc = await session.run(script, [token, str(tail.seen), *argv], tail, root=True)
        except LOST_ERRORS as exc:
            if attempt == 0 and tail.seen == 0 and not isinstance(exc, (ConnectionError, TimeoutError)):
                raise HostSessionError(f"cannot connect: {type(exc).__name__}: {exc}") from exc
            rc = None
        if rc is not None and rc >= 0:
            return rc, tail.lines
        await publish(f"[host:{label}] connection lost; the operation continues on the host, "
                      f"reconnecting ({attempt + 1}/{MAX_RECONNECTS})")
        await asyncio.sleep(RECONNECT_DELAY)
    raise HostSessionError("could not reconnect; the operation may still be running on the host")


def host_op_of(request: RunRequest) -> tuple[str, dict, str, str]:
    """(op, args, token, host_id). These runs exist only when posture-service asked
    for them: the template cannot be run by hand, by a schedule, a chain or a token."""
    spec = request.params.get(HOST_OP_PARAM)
    if not isinstance(spec, dict) or not isinstance(spec.get("op"), str):
        raise HostOpError("this template is run by Posture → Patching only")
    if len(request.target_host_ids) != 1:
        raise HostOpError("exactly one host per operation")
    return spec["op"], spec.get("args") or {}, check_token(request.params.get(OP_TOKEN_PARAM)), request.target_host_ids[0]


class PackageOpsExecutor(ActionExecutor):
    name = PACKAGE_ACTION
    # One run per host per step: the rollout raises its own notification.
    notify_on_success = False
    notify_on_failure = False

    def validate(self, params: dict) -> None:
        return None

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        try:
            op, args, token, host_id = host_op_of(request)
            argv = argv_for(PACKAGE_ACTION, op, args)
            session = await HostSession.resolve(host_id, request)
            rc, _ = await run_detached(session, PACKAGE_WORKER, token, argv, host_id, publish_line)
        except (HostOpError, HostSessionError) as exc:
            return RunResult(ok=False, output=f"[refused] {exc}", exit_code=1)
        return RunResult(ok=rc == 0, output="", exit_code=rc)

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))
