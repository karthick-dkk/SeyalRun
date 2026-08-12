"""Remote-line-buffer guard for the terminal command filter (see terminal.py).

Keystrokes are forwarded to the remote PTY character-by-character for echo, so by the
time a command line is judged (on its newline) the characters are ALREADY in the remote
shell's input buffer. Blocking by merely withholding the newline is not enough — a later
bare newline would execute the residual buffer. On every block decision the handler calls
``clear_remote_line`` to send Ctrl-U (the tty/readline "kill line" control), discarding the
remote line so a denied command can never run.

Kept dependency-free (no fastapi/sqlalchemy/settings) so it is unit-testable in isolation.
"""

from __future__ import annotations

KILL_LINE = b"\x15"  # Ctrl-U — tty VKILL / readline unix-line-discard


async def clear_remote_line(process) -> None:
    """Discard whatever the client already typed into the remote shell's current input line.
    Best-effort — if the PTY is already gone the session is ending anyway."""
    try:
        process.stdin.write(KILL_LINE)
        await process.stdin.drain()
    except Exception:
        pass
