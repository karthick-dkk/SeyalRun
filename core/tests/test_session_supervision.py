"""Supervision: joining, taking control, and ending someone else's session.

This is the half of PAM that answers "who was watching, and who was driving".
Read-only join already existed and **wrote no audit row at all** — a supervision
feature that leaves no trace of the supervisor is the one kind that must not,
because "who watched this session" is the question it exists to answer. It also
had no UI, so the capability was unreachable.

The properties pinned here are the ones that make supervision trustworthy rather
than merely present:

  * joining is audited, critically, BEFORE any output reaches the observer;
  * taking control is admin-only, exclusive, and ANNOUNCED to the operator whose
    session it is — silent control would attribute their commands to them while
    someone else typed;
  * a supervisor's input goes through the same stdin path an operator keystroke
    takes, so it cannot bypass the session's own controls;
  * control is released when the supervisor's socket drops, so a lost connection
    cannot leave a session seized;
  * the read-only default holds: a non-admin observer's control frames are refused.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TS = ROOT / "services/terminal-service"
SPECTATE = TS / "app/ws/spectate.py"
SUPERVISION = TS / "app/supervision.py"
TERMINAL = TS / "app/ws/terminal.py"
MAIN = TS / "app/main.py"
WATCH_VIEW = ROOT / "services/frontend/src/views/SessionWatchView.vue"
SESSIONS_VIEW = ROOT / "services/frontend/src/views/SessionsView.vue"
ROUTER = ROOT / "services/frontend/src/router/index.ts"


def _handler() -> str:
    src = SPECTATE.read_text()
    return src[src.index("async def handle_spectate"):]


def test_modules_exist():
    """Guard against every assertion below passing vacuously."""
    assert SUPERVISION.exists() and WATCH_VIEW.exists()
    assert "async def handle_spectate" in SPECTATE.read_text()


# ── joining is recorded ──────────────────────────────────────────────────────

def test_join_is_audited_critically():
    body = _handler()
    assert '"session.join"' in body, (
        "read-only join wrote no audit row — the feature exists to answer "
        "'who watched this session' and could not"
    )
    i = body.index('"session.join"')
    assert "critical=True" in body[i - 400: i + 500], "observing a session unlogged is the failure mode"


def test_join_is_audited_before_any_output_is_streamed():
    """Auditing after the fact records only the sessions someone finished
    watching, not the ones they looked at and left."""
    body = _handler()
    join_at = body.index('"session.join"')
    accept_at = body.index("subs.append(websocket)")
    assert accept_at < join_at, "expected the join row right after subscribing"
    assert "websocket.close(code=4500)" in body[join_at: join_at + 900], \
        "a join that cannot be logged must not proceed"


# ── takeover ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("action", ["takeover", "release", "input", "terminate"])
def test_control_actions_are_admin_only(action: str):
    gate = re.search(r'if kind in \(([^)]*)\) and role not in \("admin", "superadmin"\)', _handler())
    assert gate, "no admin gate found for supervision actions"
    assert f'"{action}"' in gate.group(1), f"{action} is not behind the admin gate"


def test_takeover_is_exclusive():
    assert "ctl.taken and ctl.controller_id != user_id" in _handler(), (
        "two supervisors typing into one session at once is not supervision"
    )


def test_takeover_notifies_the_operator():
    """Silent control would put the operator's name on commands they did not type
    — the exact attribution the audit chain exists to get right."""
    body = _handler()
    take = body[body.index('"session.takeover"'):][:900]
    assert "ctl.notify(" in take, "the operator must be told their session was taken over"
    assert "has taken control" in body


def test_takeover_and_release_are_audited():
    body = _handler()
    assert '"session.takeover"' in body
    assert '"session.takeover_release"' in body, "handing control back must also be recorded"
    assert "critical=True" in body[body.index('"session.takeover"'):][:600]


def test_supervisor_input_requires_holding_control():
    body = _handler()
    inp = body[body.index('elif kind == "input"'):][:400]
    assert "ctl.controller_id != user_id" in inp, (
        "input must be refused unless this supervisor currently holds control"
    )


def test_supervisor_input_uses_the_same_path_as_a_keystroke():
    """A takeover that could run what the session's own filters would refuse
    would be privilege escalation dressed as a supervision feature."""
    term = TERMINAL.read_text()
    fn = term[term.index("async def _supervisor_write"):][:900]
    assert "process.stdin.write" in fn, "supervisor input must go through the session's stdin"
    for bypass in ("ssh_conn.run", "create_process", "exec_command"):
        assert bypass not in fn, f"supervisor input must not open its own channel ({bypass})"


def test_control_is_released_when_the_supervisor_disconnects():
    """A dropped socket must not leave the session seized forever."""
    body = _handler()
    assert "release_if_controller" in body[body.rindex("finally:"):]


def test_release_only_affects_the_actual_controller():
    src = SUPERVISION.read_text()
    fn = src[src.index("def release_if_controller"):]
    assert "ctl.controller_id == user_id" in fn, (
        "one supervisor must not be able to release another's control"
    )


# ── termination from the joined view ─────────────────────────────────────────

def test_terminate_from_supervision_is_audited_with_the_actor():
    body = _handler()
    term = body[body.index('elif kind == "terminate"'):][:900]
    assert '"session.terminate"' in term
    assert "critical=True" in term
    assert '"source": "supervision"' in term, (
        "the row must distinguish an in-session kill from the REST one, or two "
        "different actions look identical in the log"
    )


def test_terminate_records_failure_when_there_is_nothing_to_terminate():
    body = _handler()
    term = body[body.index('elif kind == "terminate"'):][:900]
    assert 'result="success" if ev else "failure"' in term


def test_spectate_receives_the_terminate_registry():
    """Without it the terminate branch silently does nothing."""
    # The call site, not the import line — split() yields both, and the import
    # naturally has no arguments.
    src = MAIN.read_text()
    call = src[src.index("await handle_spectate("):]
    call = call[: call.index(")") + 1]
    assert "_terminate_events" in call, f"spectate is called without the registry: {call}"


# ── read-only default ────────────────────────────────────────────────────────

def test_non_admin_observers_stay_read_only():
    body = _handler()
    assert '"denied"' in body, "a refused supervision action must say so rather than be ignored"
    assert re.search(r'if kind in \([^)]*\) and role not in \("admin", "superadmin"\)', body)


# ── the capability is reachable ──────────────────────────────────────────────

def test_watch_route_precedes_the_playback_route():
    """/sessions/:id would otherwise swallow /sessions/:id/watch."""
    src = ROUTER.read_text()
    assert "session-watch" in src, "the supervision view must be routed"
    assert src.index("/sessions/:id/watch") < src.index("'/sessions/:id'"), (
        "the watch route must be declared before the :id playback route"
    )


def test_sessions_list_links_to_the_live_view():
    """The capability existed with no UI at all — it was unreachable."""
    src = SESSIONS_VIEW.read_text()
    assert "/watch" in src and "Watch" in src


def test_watch_view_only_sends_input_while_holding_control():
    src = WATCH_VIEW.read_text()
    assert "controller.value === myName.value" in src, (
        "the client must not send keystrokes unless it holds control"
    )
    assert "type: 'input'" in src


def test_watch_view_tells_the_supervisor_what_is_recorded():
    """Someone typing into another person's session should know it is attributed
    to them."""
    assert "recorded against you" in WATCH_VIEW.read_text()
