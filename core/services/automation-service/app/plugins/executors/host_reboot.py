"""Executor: host state snapshot, and reboot-then-wait-until-back (3.0 M4)."""

from __future__ import annotations

import asyncio
import base64
import gzip
import time
from collections.abc import Callable
from pathlib import Path

from libs.pluginbase import ActionExecutor, RunRequest, RunResult

from app._host_ops import REBOOT_ACTION, HostOpError, argv_for, wait_seconds
from app._host_session import LOST_ERRORS, HostSession, HostSessionError
from app.plugins.executors.package_ops import host_op_of, run_detached

_DIR = Path(__file__).parent
SNAPSHOT_SCRIPT = (_DIR / "host_snapshot.sh").read_text()
REBOOT_WORKER = (_DIR / "host_reboot.sh").read_text()
POLL = 10
SETTLE_ATTEMPTS = 2     # consecutive good snapshots before the host counts as back


def marker_in(lines: list[str]) -> str:
    """The boot marker inside a snapshot's payload, or ""."""
    try:
        b = next(i for i, ln in enumerate(lines) if ln.startswith("SEYALRUN-SNAP-BEGIN v1")) + 1
        e = lines.index("SEYALRUN-SNAP-END")
        raw = base64.b64decode("".join(lines[b:e]))
        text = (gzip.decompress(raw) if lines[b - 1].endswith(" gz") else raw).decode("utf-8", "replace")
    except (StopIteration, ValueError, OSError, EOFError):
        return ""
    if "\nZ\tend" not in text:
        return ""
    return next((ln[2:] for ln in text.splitlines() if ln.startswith("B\t")), "")


async def _snapshot(session: HostSession, urls: list[str]) -> tuple[int | None, list[str]]:
    lines: list[str] = []

    async def keep(line: str) -> None:
        lines.append(line)
    return await session.run(SNAPSHOT_SCRIPT, urls, keep, root=False), lines


class HostRebootExecutor(ActionExecutor):
    name = REBOOT_ACTION
    notify_on_success = False
    notify_on_failure = False

    def validate(self, params: dict) -> None:
        return None

    async def execute(self, request: RunRequest, publish_line: Callable) -> RunResult:
        try:
            op, args, token, host_id = host_op_of(request)
            _, *urls = argv_for(REBOOT_ACTION, op, args)
            session = await HostSession.resolve(host_id, request)
            if op == "snapshot":
                rc, lines = await _snapshot(session, urls)
                for ln in lines:
                    await publish_line(f"[host:{host_id}] {ln}")
                return RunResult(ok=rc == 0, output="", exit_code=255 if rc is None else rc)
            return await self._reboot(session, token, urls, host_id, wait_seconds(args), publish_line)
        except (HostOpError, HostSessionError) as exc:
            return RunResult(ok=False, output=f"[refused] {exc}", exit_code=1)
        except LOST_ERRORS as exc:
            return RunResult(ok=False, output=f"[host:{host_id}] connection failed: {type(exc).__name__}: {exc}",
                             exit_code=255)

    async def _reboot(self, session: HostSession, token: str, urls: list[str], host_id: str, wait: int,
                      publish: Callable) -> RunResult:
        rc, lines = await run_detached(session, REBOOT_WORKER, token, [], host_id, publish)
        before = next((ln.split(" ", 1)[1] for ln in lines if ln.startswith("SEYALRUN-MARKER ")), "")
        if rc != 0 or not before:
            return RunResult(ok=False, output="", exit_code=rc or 1)
        deadline, good, last = time.monotonic() + wait, 0, ""
        await publish(f"[host:{host_id}] waiting up to {wait}s for the host to come back")
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL)
            try:
                src, snap = await asyncio.wait_for(_snapshot(session, urls), timeout=90)
            except LOST_ERRORS as exc:
                good, last = 0, type(exc).__name__
                continue
            marker = marker_in(snap)
            if src != 0 or not marker or marker == before:
                good, last = 0, "still on the old boot" if marker == before else "no snapshot yet"
                continue
            good += 1
            if good >= SETTLE_ATTEMPTS:
                await publish(f"[host:{host_id}] SEYALRUN-REBOOTED the host is back on a new boot")
                for ln in snap:
                    await publish(f"[host:{host_id}] {ln}")
                return RunResult(ok=True, output="", exit_code=0)
        return RunResult(ok=False, output=f"[host:{host_id}] the host did not come back within {wait}s ({last})",
                         exit_code=76)

    async def run(self, request: RunRequest) -> RunResult:
        return await self.execute(request, lambda _: asyncio.sleep(0))
