"""Live session control, keyed by session id — Increment 2 (supervision).

This is the half of PAM that answers "who was watching, and who was driving".
Read-only join already existed (ws/spectate.py); what was missing was a record
of it and any way to intervene.

Three capabilities, deliberately separated because they are different levels of
intrusion into someone else's session:

  join      observe output. Already existed, wrote NO audit row — a supervision
            feature that leaves no trace of the supervisor is the one kind that
            must not, since "who watched this session" is the question it exists
            to answer.
  takeover  send input. One controller at a time, and the operator whose session
            it is IS TOLD, in their own terminal. A PAM that let an administrator
            silently drive someone else's shell would produce audit rows naming
            the wrong person for every command typed.
  terminate end the session from inside the joined view.

In-process, like sftp_registry and main.py's _spectators: a second instance of
this service would not see controls owned by the first. Sessions are already
pinned to the instance holding their WebSocket, so this is the same existing
scaling caveat rather than a new one.
"""

from __future__ import annotations

from typing import Awaitable, Callable

WriteFn = Callable[[str], Awaitable[bool]]
NotifyFn = Callable[[str], Awaitable[None]]


class SessionControl:
    """Handles into one live session, published by ws/terminal.py."""

    def __init__(self, write: WriteFn, notify: NotifyFn, owner_id: str, owner_name: str):
        self.write = write            # push input through the SAME path a keystroke takes
        self.notify = notify          # write a line into the operator's own terminal
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.controller_id: str | None = None
        self.controller_name: str = ""

    @property
    def taken(self) -> bool:
        return self.controller_id is not None


_controls: dict[str, SessionControl] = {}


def register(session_id: str, control: SessionControl) -> None:
    _controls[session_id] = control


def unregister(session_id: str) -> None:
    """Idempotent — teardown paths may run more than once."""
    _controls.pop(session_id, None)


def get(session_id: str) -> SessionControl | None:
    return _controls.get(session_id)


def release_if_controller(session_id: str, user_id: str) -> bool:
    """Drop control if this user holds it. Called when a supervisor's socket
    closes, so a dropped connection cannot leave a session permanently seized."""
    ctl = _controls.get(session_id)
    if ctl and ctl.controller_id == user_id:
        ctl.controller_id = None
        ctl.controller_name = ""
        return True
    return False
