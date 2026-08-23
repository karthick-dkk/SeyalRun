"""The terminal lists only hosts you can actually connect to, and SFTP means SFTP.

Three defects, all reported from the UI:

  * every host in inventory was listed, including ones with no authorization.
    Session create refuses correctly so nothing was exposed — but a list where
    most entries fail is not a list, and it misrepresents the access model to
    whoever reads it. An operator reasonably reads "these are my hosts".

  * a host with an authorization but NO credential is equally dead: the picker
    opens empty and there is nothing to click.

  * choosing SFTP still showed the terminal beside the file browser. The session
    has to keep running (transfer rides its SSH connection) but it should not be
    on screen, and the browser should have the whole pane.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "services/terminal-service/app/api/sessions.py"
VIEW = ROOT / "services/frontend/src/views/TerminalView.vue"
PANES = ROOT / "services/frontend/src/composables/useTerminalPanes.ts"


def _fn(name: str) -> str:
    src = SESSIONS.read_text()
    body = src[src.index(f"async def {name}"):]
    return body[: body.index("\n@router")]


def test_endpoint_exists():
    """Guard against every assertion below passing vacuously."""
    assert '@router.get("/ssh/hosts")' in SESSIONS.read_text()


# ── only connectable hosts ───────────────────────────────────────────────────

def test_host_list_is_resolved_strictly():
    """The list must not promise more than session create allows. strict=true is
    what stops a role bypass making a host appear that the gate then refuses."""
    body = _fn("connectable_hosts")
    assert "/internal/authz/host-ids" in body
    assert 'strict="true"' in body, "role-based visibility would contradict the gate"


def test_host_list_requires_a_usable_login():
    """'Authorized' is not enough — a host with no credential opens an empty
    picker. This is the 'account is attached to host' half."""
    body = _fn("connectable_hosts")
    assert "credential_ids" in body
    assert re.search(r"if not cred_ids and \"manual_account\" not in actions", body), (
        "a host with no credential and no manual grant must be dropped"
    )


def test_host_list_respects_the_ssh_action():
    body = _fn("connectable_hosts")
    assert 'if actions and "ssh" not in actions' in body, (
        "a grant of sftp-only must not put the host in the SSH terminal list"
    )


def test_terminal_uses_the_filtered_endpoint():
    src = VIEW.read_text()
    assert "api.get('/ssh/hosts')" in src, "the terminal must load the authorized list"
    assert "api.get('/hosts')" not in src, "the unfiltered inventory list must not be used here"


# ── SFTP is SFTP ─────────────────────────────────────────────────────────────

def test_pane_carries_a_mode():
    src = PANES.read_text()
    assert "mode: 'ssh' | 'sftp'" in src
    assert "mode: 'ssh'" in src, "panes must default to a terminal"


def test_sftp_hides_the_terminal_without_unmounting_it():
    """Unmounting closes the WebSocket that holds the SSH connection open — and
    the file browser works over exactly that connection. Hidden, not destroyed."""
    src = VIEW.read_text()
    assert "'pane-hidden': activePane.mode === 'sftp'" in src
    assert "'pane-hidden': splitPane.mode === 'sftp'" in src, "the split side too"
    assert re.search(r"\.pane-hidden\s*\{\s*display:\s*none", src)
    # the component must still be rendered when a session exists
    assert 'v-if="activePane?.session"' in src, "TermSession must stay mounted in sftp mode"


def test_file_browser_takes_the_full_pane_in_sftp_mode():
    src = VIEW.read_text()
    assert "'fm-full': focusedPane.mode === 'sftp'" in src
    assert re.search(r"\.fm-full\s*\{[^}]*width:\s*100%", src)


def test_closing_the_browser_ends_the_sftp_session():
    """An SFTP pane ENDS when its browser closes — it must not fall back to a
    terminal. Falling back hands the operator an interactive shell they never
    asked for, on a host they chose to reach for file transfer only. "I closed
    the file manager" is not a request for a shell.

    (This asserted the opposite until the behaviour was reported: the earlier
    reasoning was "an empty pane is useless", which weighed tidiness above not
    handing out a shell.)"""
    src = VIEW.read_text()
    fn = src[src.index("async function onCloseFiles"):]
    fn = fn[: fn.index("\n}\n") + 3]
    assert "pane.session = null" in fn, "the pane must not keep showing the session"
    assert "api.delete(`/ssh/sessions/" in fn, (
        "the SSH session exists only to carry SFTP — leaving it running keeps a "
        "credential unwrapped and shows a live session nobody is watching"
    )


def test_sftp_close_does_not_leave_the_pane_on_the_host():
    """A pane still bound to hostId would reconnect on the next click as though
    the previous session were being resumed."""
    src = VIEW.read_text()
    fn = src[src.index("async function onCloseFiles"):]
    fn = fn[: fn.index("\n}\n") + 3]
    assert "pane.hostId = null" in fn


# ── right-click offers both ──────────────────────────────────────────────────

def test_context_menu_offers_ssh_and_sftp_per_login():
    src = VIEW.read_text()
    assert "ctxConnectWithCred(cred, 'ssh')" in src
    assert "ctxConnectWithCred(cred, 'sftp')" in src
    fn = src[src.index("function ctxConnectWithCred"):]
    fn = fn[: fn.index("\n}") + 2]
    assert "pane.mode = mode" in fn, "the chosen mode must reach the pane"
    assert "showFiles.value = true" in fn


def test_single_click_autoconnects_with_the_saved_login():
    """The remembered login is what makes one click enough."""
    src = VIEW.read_text()
    fn = src[src.index("async function connectWithCredPicker"):]
    fn = fn[: fn.index("\n}\n") + 3]
    assert "rememberedCredential(host.id)" in fn
    assert "await connectPane(paneId, host, saved)" in fn, "a saved login must skip the picker"
