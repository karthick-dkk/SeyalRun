"""Live SSH connections, keyed by session id, so SFTP can ride them.

The parity plan is explicit that SFTP runs "over the existing asyncssh
connection, reusing the same credential unwrap and the same SessionTarget; no
second auth path". This registry is what makes that possible: ws/terminal.py
publishes the connection it already opened, and api/sftp.py borrows it.

Why not open a second connection per file operation:

  * It would be a second authentication, which means a second place credentials
    are unwrapped and a second thing to get wrong. The whole point of the
    constraint is that there is one.
  * The ProxyJump chain (zone gateways, resolved live at connect time) would
    have to be rebuilt per request — the expensive part of connecting.
  * A file transfer that succeeded while the session it belongs to was already
    closed would be unattributable in the audit chain.

In-process only, exactly like main.py's _spectators: a second instance of this
service would not see connections owned by the first. That is fine today
(sessions are already pinned to the instance holding their WebSocket) and is the
same scaling caveat, recorded here so it is not rediscovered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:                      # pragma: no cover - typing only
    import asyncssh

# session_id -> live connection owned by that session's terminal WebSocket.
_connections: dict[str, "asyncssh.SSHClientConnection"] = {}


def register(session_id: str, conn: "asyncssh.SSHClientConnection") -> None:
    _connections[session_id] = conn


def unregister(session_id: str) -> None:
    """Idempotent — teardown paths may run more than once."""
    _connections.pop(session_id, None)


def get(session_id: str) -> "asyncssh.SSHClientConnection | None":
    return _connections.get(session_id)


def active_session_ids() -> list[str]:
    return list(_connections)
