"""P1 fix: a blocked terminal command must not survive in the remote shell's input
line. clear_remote_line sends Ctrl-U (kill line) so residual keystrokes can't be
executed by a later newline. Pure/dependency-free — testable in isolation."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture
def guard(load_module):
    return load_module("services/terminal-service/app/ws/_lineguard.py", "za_lineguard")


class _FakeStdin:
    def __init__(self, fail: bool = False):
        self.written = b""
        self._fail = fail
        self.drained = 0

    def write(self, b: bytes):
        if self._fail:
            raise BrokenPipeError("pty gone")
        self.written += b

    async def drain(self):
        self.drained += 1


class _FakeProc:
    def __init__(self, fail: bool = False):
        self.stdin = _FakeStdin(fail)


def test_kill_line_is_ctrl_u(guard):
    assert guard.KILL_LINE == b"\x15"


def test_clear_sends_ctrl_u_and_drains(guard):
    proc = _FakeProc()
    asyncio.run(guard.clear_remote_line(proc))
    assert proc.stdin.written == b"\x15"   # blocked command's residual line is killed
    assert proc.stdin.drained == 1         # flushed to the PTY immediately


def test_clear_is_exception_safe_when_pty_dead(guard):
    # A dead PTY must not raise into the handler (the session is ending anyway).
    proc = _FakeProc(fail=True)
    asyncio.run(guard.clear_remote_line(proc))  # must not raise
