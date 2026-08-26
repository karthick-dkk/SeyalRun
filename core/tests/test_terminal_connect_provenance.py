"""How the terminal decides whether an autoconnect was started INSIDE the app or
followed from OUTSIDE it (a Zabbix deep link, a pasted URL).

Regression: /qa on 2026-08-26. Report: .gstack/qa-reports/qa-report-192-168-64-7-2026-08-26.md

The bug: the Hosts/Assets terminal icon opened the terminal with
``host_id=..&autoconnect=1`` and TerminalView.handleUrlParams() routed EVERY
autoconnect through confirmZbxConnect(), which hard-sets deepLink=true. So every
in-app connect showed "Requested from Zabbix" and — because a deep link must never
auto-connect — the "remember this login and connect automatically" feature never
fired.

Three properties are load-bearing and are what these tests pin:

  * the in-app opener and the Zabbix host module build the SAME
    ``host_id=..&autoconnect=1`` URL (modules/zabbix/seyalrun/actions/Hosts.php),
    so the URL cannot tell them apart and a query flag would be FORGEABLE by any
    such link. The signal must be same-origin state a cross-origin link cannot
    write — a localStorage marker — not a URL parameter.

  * the marker is consumed on read, so the opener's own tab wins any race and a
    stale marker cannot be replayed; and it is time-bounded.

  * absence of the marker is the safe default: the deep-link path, which always
    requires an explicit login choice.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FE = ROOT / "services/frontend/src"
CLIENT = FE / "api/client.ts"
TERMINAL = FE / "views/TerminalView.vue"
HOSTS = FE / "views/HostsView.vue"
ASSETS = FE / "views/AssetsView.vue"


def _code_only(text: str) -> str:
    """Drop // line comments and /* */ blocks so a check matches CODE, not the
    prose ABOUT the code — the recurring failure mode of this repo's own tests."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("//")
    )


# ── the marker mechanism, EXECUTED (not grepped) ─────────────────────────────

def _extract_handshake_js() -> str:
    """Pull the real mark/consume implementation out of client.ts and strip the
    TS annotations so node can run the SAME source the app ships."""
    src = CLIENT.read_text()
    start = src.index("const INTERNAL_CONNECT_TTL_MS")
    # brace-walk from consumeInternalConnect (the LAST of the two) to its close,
    # so the extracted slice covers BOTH functions plus the TTL constant.
    consume_at = src.index("export function consumeInternalConnect")
    depth = 0
    started = False
    end = None
    for i in range(consume_at, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
            started = True
        elif ch == "}":
            depth -= 1
            if started and depth == 0:
                end = i + 1
                break
    js = src[start:end]
    js = js.replace("export ", "")
    js = re.sub(r":\s*(string|void|boolean)\b", "", js)  # drop return/param types
    return js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_marker_roundtrip_is_internal_then_consumed():
    js = _extract_handshake_js()
    harness = (
        "const _store = new Map();\n"
        "globalThis.localStorage = {\n"
        "  getItem: k => (_store.has(k) ? _store.get(k) : null),\n"
        "  setItem: (k, v) => _store.set(k, String(v)),\n"
        "  removeItem: k => _store.delete(k),\n"
        "};\n"
        + js + "\n"
        "const out = [];\n"
        # fresh in-app open -> internal, exactly once
        "markInternalConnect('h1');\n"
        "out.push(['fresh', consumeInternalConnect('h1') === true]);\n"
        # consume-on-read: a second read (or a forged replay) is NOT internal
        "out.push(['consumed', consumeInternalConnect('h1') === false]);\n"
        # a link that never wrote our storage (Zabbix / pasted URL) -> deep link
        "out.push(['no-marker', consumeInternalConnect('other') === false]);\n"
        # stale marker beyond the TTL -> deep link\n"
        "_store.set('sr_ti:h2', String(Date.now() - 60000));\n"
        "out.push(['stale', consumeInternalConnect('h2') === false]);\n"
        "console.log(JSON.stringify(out));\n"
    )
    res = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert res.returncode == 0, res.stderr
    results = dict(__import__("json").loads(res.stdout.strip().splitlines()[-1]))
    assert results == {
        "fresh": True, "consumed": True, "no-marker": True, "stale": True
    }, f"handshake behaved wrong: {results}"


# ── the signal is same-origin state, NOT a forgeable URL flag ────────────────

def test_marker_is_localstorage_not_a_url_flag():
    code = _code_only(CLIENT.read_text())
    mark = code[code.index("function markInternalConnect"):]
    mark = mark[: mark.index("\n}") + 2]
    assert "localStorage.setItem" in mark, "the marker must be same-origin storage"
    # A URL query flag would be forgeable by the very deep links this guards against.
    for forgeable in ("src=app", "internal=1", "&src=", "&internal="):
        assert forgeable not in CLIENT.read_text(), f"marker leaked into the URL ({forgeable})"


def test_in_app_openers_mark_before_opening():
    for view in (HOSTS, ASSETS):
        code = _code_only(view.read_text())
        # the window.open of the terminal must be preceded by a mark for that host
        # ([^\n]* tolerates a trailing // comment after the mark call)
        m = re.search(r"markInternalConnect\([^)]*\)[^\n]*\n\s*window\.open\(\s*terminalUrl", code)
        assert m, f"{view.name}: the in-app opener must markInternalConnect() before window.open()"
        # and it must still NOT put a provenance flag in the URL
        opener = code[code.index("terminalUrl(`host_id="):][:160]
        assert "src=" not in opener and "internal=" not in opener


# ── handleUrlParams routes internal vs deep-link correctly ───────────────────

def _handle_url_params() -> str:
    src = _code_only(TERMINAL.read_text())
    body = src[src.index("function handleUrlParams"):]
    return body[: body.index("\n}\n") + 3] if "\n}\n" in body else body[:1200]


def test_only_a_marked_internal_open_takes_the_in_app_path():
    fn = _handle_url_params()
    # internal decision: consume the marker AND rule out an external deep link
    assert "consumeInternalConnect(target.id)" in fn, \
        "the in-app decision must consume the same-origin marker"
    assert "!zbxHost" in fn and "!ltToken" in fn, \
        "a zbx_host/lt deep link must never be treated as internal even if a marker is present"
    # the two destinations: internal -> normal picker (honours remembered login),
    # everything else -> the deep-link path (explicit choice, no auto-connect)
    assert "connectWithCredPicker(activePaneId.value, target)" in fn, \
        "an internal open must use the normal picker, which honours a remembered login"
    assert "confirmZbxConnect(activePaneId.value, target)" in fn, \
        "a deep link must still take the explicit-choice path"


def test_deep_link_path_still_requires_explicit_choice():
    """The safe default must be preserved: confirmZbxConnect sets deepLink=true,
    and the CredentialPicker only auto-connects when it is false."""
    src = TERMINAL.read_text()
    fn = src[src.index("async function confirmZbxConnect"):]
    fn = fn[: fn.index("\n}\n") + 3]
    assert "credPicker.deepLink = true" in fn
