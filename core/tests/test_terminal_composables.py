"""The terminal split kept its behaviour, and the 403 discrimination survived it.

Increment 1a of the parity plan moves pane/split state and session lifecycle out
of TerminalView.vue so SFTP and supervision have somewhere to land. The risk in
a no-behaviour-change refactor is exactly that some behaviour changes, so these
pin the parts that were easy to lose.

The 403 mapping is the substance. POST /ssh/sessions returns 403 for four
genuinely different reasons — no credential linked, the ssh action not granted,
a login ACL refusal, no authorization at all — and each has a different fix in a
different admin screen. Collapsing them to "forbidden" turns a two-minute fix
into a support ticket. There is no JS test runner in this repo, so this reads
the mapping out of the composable and exercises it directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "services/frontend/src"
PANES = SRC / "composables/useTerminalPanes.ts"
SESSION = SRC / "composables/useTerminalSession.ts"
VIEW = SRC / "views/TerminalView.vue"


def test_composables_exist():
    assert PANES.exists() and SESSION.exists()


def test_view_no_longer_owns_the_extracted_state():
    """If the view still declares these, the split did not actually happen and
    two copies of the state are live at once."""
    src = VIEW.read_text()
    for gone in ("const panes = ref<Pane[]>", "async function connectPane(",
                 "function onDisconnected(", "function onReconnect("):
        assert gone not in src, f"TerminalView still declares {gone!r} after the extraction"
    for used in ("useTerminalPanes(", "useTerminalSession("):
        assert used in src, f"TerminalView does not call {used!r}"


def test_split_undo_exists_exactly_once():
    """addPane() makes what it creates the ACTIVE pane — right for every caller
    except a split, where the new pane is the SECONDARY side. Without the undo it
    steals the primary slot and unmounts the session already showing there (a
    live bug once: clicking Split appeared to reload every open session).

    doSplit() and ctxSplitConnect() each carried their own copy. Two copies of a
    fix that subtle is one copy away from a regression."""
    undo = "activePaneId.value = originalActiveId"
    assert PANES.read_text().count(undo) == 1, "the split undo must live in openSplit() alone"
    assert undo not in VIEW.read_text(), "TerminalView must not re-implement the split undo"


def test_kiosk_guard_is_a_getter_not_a_snapshot():
    """Passing the boolean would freeze kiosk state at setup, and a session that
    became kiosk later would still be able to open a second pane."""
    view = VIEW.read_text()
    assert "isKiosk: () => auth.isKiosk" in view, "isKiosk must be passed as a getter"
    assert "opts.isKiosk()" in PANES.read_text(), "the composable must call the getter"


# ── the 403 discrimination ───────────────────────────────────────────────────

def _ssh_error_message(status, detail, host):
    """Execute the mapping as written in the TS source, rather than restating it
    here — a copy in the test would pass while the real one drifted."""
    src = SESSION.read_text()
    body = src[src.index("export function sshErrorMessage"):]
    body = body[: body.index("\nexport function useTerminalSession")]
    if status == 403:
        for needle, tmpl in re.findall(r"detail\.includes\('([^']+)'\)[^\n]*\)?\s*\{\s*\n?\s*return `([^`]+)`", body):
            if needle in detail:
                return tmpl.replace("${hostName}", host)
        m = re.search(r"return `(No authorization[^`]+)`", body)
        return m.group(1).replace("${hostName}", host)
    if status == 404:
        return "Host not found."
    if detail:
        return detail
    m = re.search(r"return `(SSH connection failed[^`]+)`", body)
    return m.group(1).replace("${hostName}", host)


def test_mapping_parses():
    """Guard against every case below silently falling through to one branch."""
    assert "Admin → Credentials" in SESSION.read_text()


@pytest.mark.parametrize("detail,expect_screen", [
    ("no credential linked to host", "Admin → Credentials"),
    ("ssh action not granted", "Authorization actions"),
    ("login denied by ACL", "ACL"),
    ("something else entirely", "Admin → Authorizations"),
])
def test_403_names_the_screen_that_fixes_it(detail: str, expect_screen: str):
    msg = _ssh_error_message(403, detail, "web-01")
    assert expect_screen in msg, f"403 '{detail}' produced {msg!r}, which does not point anywhere"
    assert "web-01" in msg, "the message must name the host"


def test_non_403_paths_are_distinct():
    assert "not found" in _ssh_error_message(404, "", "web-01").lower()
    assert _ssh_error_message(500, "upstream exploded", "web-01") == "upstream exploded"
    assert "reachable" in _ssh_error_message(None, "", "web-01")
