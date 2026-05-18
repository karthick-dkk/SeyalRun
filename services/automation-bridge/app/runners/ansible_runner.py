"""Ansible runner — wraps ansible-runner library with security controls."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import ansible_runner
import structlog

from ..models import AnsibleRunRequest, JobStatus

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

# Patterns that suggest credential leaks in ansible output
_CREDENTIAL_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|api_key|token)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN\s+(?:RSA|EC|OPENSSH)\s+PRIVATE\s+KEY"),
]

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

MAX_LINE_LENGTH = 2048


def _sanitize_line(line: str) -> str | None:
    """Strip ANSI, truncate, and drop lines containing credential patterns."""
    line = _ANSI_ESCAPE.sub("", line).rstrip()

    if len(line) > MAX_LINE_LENGTH:
        line = line[:MAX_LINE_LENGTH] + " ...[truncated]"

    for pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(line):
            return "[redacted: possible credential in output]"

    return line if line else None


class AnsibleJobRunner:
    """Executes ansible-runner jobs asynchronously."""

    def __init__(self, playbooks_dir: Path, timeout: int = 300) -> None:
        self.playbooks_dir = playbooks_dir
        self.timeout = timeout

    async def run(
        self,
        request: AnsibleRunRequest,
        job_id: uuid.UUID,
        on_output: asyncio.Queue,
    ) -> tuple[JobStatus, int, list[str]]:
        """Run the playbook and stream output lines into on_output queue.

        Returns (status, exit_code, all_lines).
        """
        playbook_path = self.playbooks_dir / request.playbook
        if not playbook_path.exists():
            log.error("playbook_not_found", playbook=request.playbook)
            return JobStatus.FAILED, 1, [f"Playbook not found: {request.playbook}"]

        log.info(
            "ansible_job_start",
            job_id=str(job_id),
            playbook=request.playbook,
            triggered_by=request.triggered_by,
        )

        all_lines: list[str] = []
        started = time.monotonic()

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_sync,
                    request,
                    job_id,
                    all_lines,
                    on_output,
                ),
                timeout=request.timeout_seconds,
            )
            exit_code: int = result
        except TimeoutError:
            log.warning("ansible_job_timeout", job_id=str(job_id))
            return JobStatus.TIMEOUT, -1, all_lines
        except Exception as exc:
            log.error("ansible_job_error", job_id=str(job_id), error=str(exc))
            return JobStatus.FAILED, -1, all_lines

        duration = time.monotonic() - started
        log.info(
            "ansible_job_complete",
            job_id=str(job_id),
            exit_code=exit_code,
            duration_seconds=round(duration, 2),
        )

        status = JobStatus.SUCCESS if exit_code == 0 else JobStatus.FAILED
        return status, exit_code, all_lines

    def _run_sync(
        self,
        request: AnsibleRunRequest,
        job_id: uuid.UUID,
        all_lines: list[str],
        output_queue: asyncio.Queue,
    ) -> int:
        """Synchronous ansible-runner call (runs in thread pool)."""
        runner = ansible_runner.run(
            playbook=request.playbook,
            private_data_dir=str(self.playbooks_dir.parent),
            extravars=request.extra_vars,
            limit=request.inventory_selector,
            quiet=False,
            ident=str(job_id),
            event_handler=lambda event: self._handle_event(event, all_lines, output_queue),
        )
        return runner.rc if runner.rc is not None else 1

    def _handle_event(
        self,
        event: dict,
        all_lines: list[str],
        output_queue: asyncio.Queue,
    ) -> None:
        stdout = event.get("stdout", "")
        if not stdout:
            return
        for raw_line in stdout.splitlines():
            clean = _sanitize_line(raw_line)
            if clean is not None:
                all_lines.append(clean)
                try:
                    output_queue.put_nowait(clean)
                except asyncio.QueueFull:
                    pass  # Drop if consumer is too slow — don't block runner
