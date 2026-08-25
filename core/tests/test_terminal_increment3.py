"""Increment 3: watermark, ZMODEM gate, shortcut map.

The ZMODEM part is a security control, not a feature. ZMODEM (rz/sz) is a file
transfer that runs INSIDE the interactive session, so it is a second transfer
channel parallel to SFTP and subject to none of its controls: it does not go
through the upload/download grants, it does not land in the /tmp drop point, and
it writes no sftp.* row. Shipping SFTP with a jail and an audit trail while
leaving ZMODEM open is an audited front door beside an unaudited one — R-11's
failure in a different shape, and it would quietly undo the constraint that
uploads come only from the browser.

So it is blocked by default and audited either way, and the detector is EXECUTED
here rather than asserted to exist.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TS = ROOT / "services/terminal-service"
ZMODEM = TS / "app/ws/_zmodem.py"
TERMINAL = TS / "app/ws/terminal.py"
CONFIG = TS / "app/config.py"
VIEW = ROOT / "services/frontend/src/views/TerminalView.vue"
WATERMARK = ROOT / "services/frontend/src/components/terminal/TermWatermark.vue"


def _template_of(src: str) -> str:
    """The <template> block only — bindings live there, and prose about a fixed
    binding lives in <script>."""
    i = src.find("<template>")
    j = src.rfind("</template>")
    return src[i:j] if i != -1 and j != -1 else src


def _load_zmodem():
    spec = importlib.util.spec_from_file_location("_zmodem_under_test", ZMODEM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


Z = _load_zmodem()


# ── the detector, executed ───────────────────────────────────────────────────

def test_module_loads():
    assert Z.ZMODEM_CANCEL and Z.notice()


@pytest.mark.parametrize("payload", [
    "**\x18B00000000000000",              # ZHEX header — how sz announces itself
    "**\x18A",                             # ZBIN
    "**\x18C",                             # ZBIN32
    "prompt$ sz file.txt\r\n**\x18B0100",  # mid-stream, after the command echoes
    "\x1b[0m**\x18B00",                    # after an ANSI sequence
])
def test_detects_zmodem_start(payload: str):
    assert Z.detect(payload) is True


@pytest.mark.parametrize("payload", [
    "", "no transfer here", "** stars ** but no ZDLE",
    "rz",                          # merely naming the command is not a transfer
    "sz file.txt",                 # the command line alone — the frame is what counts
    "**\x18",                      # truncated header, no format byte
    "**\x18Z",                     # not a ZMODEM format byte
])
def test_does_not_fire_on_ordinary_output(payload: str):
    assert Z.detect(payload) is False, f"false positive on {payload!r}"


def test_detection_is_on_framing_not_the_command_name():
    """So it catches sz/rz however they were invoked — alias, script, wrapper."""
    src = ZMODEM.read_text()
    pattern = re.search(r'_ZMODEM_START = re\.compile\(r"([^"]+)"\)', src).group(1)
    assert "rz" not in pattern and "sz" not in pattern
    assert r"\x18" in pattern, "the pattern must match ZDLE"


def test_cancel_sequence_is_a_real_zmodem_abort():
    assert Z.ZMODEM_CANCEL.startswith("\x18" * 8), "eight CANs is the canonical abort"


def test_notice_points_at_the_supported_path():
    """Obstruction without a route forward just gets worked around."""
    n = Z.notice()
    assert "Files" in n and "blocked" in n.lower()


# ── there is no transport, and no way to switch one on ───────────────────────

def test_zmodem_transport_is_gone():
    """SFTP is the only transfer path. A working ZMODEM transport was built and
    then removed on purpose: its audit row cannot name the file (the filename
    lives inside the protocol stream), `rz` writes wherever the shell is rather
    than inside sftp_root, and it needs lrzsz on every managed host. Keeping it
    behind a flag would have left a switch that quietly undoes the /tmp
    confinement."""
    zsrc = ZMODEM.read_text()
    for gone in ("MODE_ALLOW", "def direction(", "def is_finished(", "notice_denied"):
        assert gone not in zsrc, f"transport-era helper still present: {gone}"

    tsrc = TERMINAL.read_text()
    for gone in ("zmodem_active", "zmodem_actions", "zmodem_mode"):
        assert gone not in tsrc, f"transport state still present: {gone}"

    cfg = CONFIG.read_text()
    assert "zmodem_mode" not in cfg, "a mode setting implies a mode other than blocked"


def test_no_zmodem_client_remains_in_the_frontend():
    """The client library sat between the WebSocket and xterm; leaving it there
    keeps a byte path that can corrupt terminal output for no benefit."""
    fe = ROOT / "services/frontend"
    assert not (fe / "src/composables/useZmodem.ts").exists()
    assert "zmodem" not in (fe / "package.json").read_text()
    assert "zmodem" not in (fe / "src/components/terminal/TermSession.vue").read_text().lower()


def test_every_detection_is_cancelled_and_audited():
    src = TERMINAL.read_text()
    block = src[src.index("_zmodem.detect(data)"):][:800]
    assert "ZMODEM_CANCEL" in block, "the transfer must actually be cancelled"
    assert "_audit_zmodem" in block, "the attempt must be recorded"
    assert "_zmodem.notice()" in block, "the operator must be told where to go instead"


def test_blocked_attempts_are_recorded_as_failures():
    src = TERMINAL.read_text()
    fn = src[src.index("async def _audit_zmodem"):]
    fn = fn[: fn.index("\nasync def handle_terminal")]
    assert "terminal.zmodem_blocked" in fn
    assert 'result="failure"' in fn


def test_gate_runs_before_the_data_is_recorded_or_sent():
    """Cancelling after the frame reached the browser would let it start."""
    src = TERMINAL.read_text()
    gate = src.index("_zmodem.detect(data)")
    frames = src.index('frames.append({"t": round(time.monotonic() - t0, 3), "d": data})', gate - 2000)
    assert gate < frames


# ── watermark ────────────────────────────────────────────────────────────────

def test_watermark_carries_identity_session_and_time():
    src = WATERMARK.read_text()
    for needed in ("username", "sessionId", "toISOString"):
        assert needed in src, f"watermark must include {needed}"


def test_watermark_cannot_intercept_input():
    """A deterrent overlay that swallowed clicks would break the terminal."""
    assert "pointer-events: none" in WATERMARK.read_text()


def test_watermark_is_on_by_default():
    """Opt-in would mean it is off exactly where nobody thought about it."""
    assert "const showWatermark = ref(true)" in VIEW.read_text()


def test_watermark_is_rendered_for_both_panes():
    assert VIEW.read_text().count("<TermWatermark") == 2, "split view must be watermarked too"


# ── shortcuts ────────────────────────────────────────────────────────────────

def test_shortcut_map_exists_and_is_reachable():
    src = VIEW.read_text()
    assert "const SHORTCUTS" in src
    assert "showShortcuts = true" in src, "the map must be openable from the menu"


# Every documented binding -> something in the view that must implement it.
# The first version spot-checked two entries; "Esc closes any dialog" was listed
# and did NOT close the shortcuts dialog, so the map was fiction exactly where it
# was not checked. Spot-checking a documentation test defeats its purpose.
_BINDING_EVIDENCE = {
    "Ctrl+C": "isKiosk",              # passed through to the PTY by TermSession
    "Ctrl+D": "isKiosk",
    "Ctrl+L": "isKiosk",
    "Ctrl/Cmd +": "adjustFontSize",
    "Ctrl/Cmd -": "adjustFontSize",
    "Ctrl/Cmd S": "editSnip",
    "F11": "toggleFullscreen",
    "Esc": "onWindowKeydown",   # a real window listener, not the bogus .window modifier
    "Double-click": "dblclick",
    "Double-click tab": "renameTab",
    "Right-click host": "contextmenu",
}


def test_shortcut_map_documents_real_bindings():
    """Every listed key must be one the view actually handles, or the map is
    fiction that looks like documentation."""
    src = VIEW.read_text()
    block = src[src.index("const SHORTCUTS"):]
    block = block[: block.index("\n]")]
    listed = set(re.findall(r"keys: '([^']+)'", block))
    assert listed, "no shortcuts parsed"
    undocumented = listed - set(_BINDING_EVIDENCE)
    assert not undocumented, (
        f"these are shown to users but this test has no evidence they exist: "
        f"{sorted(undocumented)} — add the handler it maps to, do not drop the check"
    )
    for keys in listed:
        marker = _BINDING_EVIDENCE[keys]
        assert marker in src, f"'{keys}' is documented but {marker!r} is not in the view"


def test_escape_closes_the_shortcut_dialog():
    """The map says Esc closes any open dialog. Esc routes to closeAll(), which
    did not touch this one — so the documented behaviour was absent."""
    src = VIEW.read_text()
    fn = src[src.index("function closeAll()"):]
    fn = fn[: fn.index("\n}") + 2]
    assert "showShortcuts.value = false" in fn


def test_escape_is_bound_on_the_window_not_an_element():
    """`.window` is NOT a Vue modifier. Vue reads an unrecognised modifier on a
    key event as another KEY name, so @keydown.esc.window put the listener on the
    element, where it only fired while focus was already inside it. After a menu
    click focus is on <body> and Esc did nothing — verified in a browser, with
    the dialog still open afterwards.

    Any element-scoped Esc binding on this view is therefore a bug, not a style
    preference."""
    src = VIEW.read_text()
    # Scoped to <template>: the fix is explained in a script comment that quotes
    # the broken binding, and a whole-file check flags the explanation as the bug.
    assert not re.search(r"@key(down|up)\.[\w.]*\.window", _template_of(src)), (
        "`.window` is not a Vue modifier — this listener is element-scoped and "
        "will not fire unless focus is already inside it"
    )
    assert "window.addEventListener('keydown', onWindowKeydown)" in src
    assert "window.removeEventListener('keydown', onWindowKeydown)" in src, \
        "an un-removed window listener outlives the view and fires on a dead component"


def test_no_view_relies_on_a_window_key_modifier():
    """Same defect, anywhere else it may have been copied to."""
    src_root = ROOT / "services/frontend/src"
    bad = [
        f"{f.relative_to(src_root)}"
        for f in src_root.rglob("*.vue")
        if re.search(r"@key(down|up)\.[\w.]*\.window", _template_of(f.read_text()))
    ]
    assert not bad, f"element-scoped listeners posing as window listeners: {bad}"
