"""Files reach a managed host one way only: chosen in a browser, dropped in /tmp.

Two constraints, both narrower than "the `upload` action is granted", and both
easy to widen later by accident — which is what these exist to prevent.

**Destination is the SFTP root itself, not the tree beneath it.** Browsing may
descend into subdirectories; writing may not. One fixed drop point is the
difference between "files can be delivered to this host" and "files can be
placed anywhere the account can write" — and the second is a far larger grant
than the word `upload` suggests to whoever approves it. crontabs, authorized_keys
and systemd units all live at writable paths.

**The bytes come from the request body and nowhere else.** No source URL, no
remote path, no host-to-host copy. A server-side fetch would let a caller move
data between machines using the PAM's credentials and network position, and the
audit row would name the operator rather than whatever actually produced the
bytes — the log would say "alice uploaded x" when alice only supplied a URL.
That is also the reason "not from other servers": the PAM must never become the
transfer agent, because then its audit trail describes the wrong actor.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SFTP = ROOT / "services/terminal-service/app/api/sftp.py"
PANEL = ROOT / "services/frontend/src/components/terminal/TermFilePanel.vue"


def _tree() -> ast.Module:
    return ast.parse(SFTP.read_text())


def _func(name: str) -> ast.AsyncFunctionDef:
    for node in _tree().body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


def _src(name: str) -> str:
    return ast.get_source_segment(SFTP.read_text(), _func(name)) or ""


def test_upload_endpoint_exists():
    """Guard against every assertion below passing vacuously."""
    assert _func("upload") is not None


# ── destination: the drop point, not the tree ────────────────────────────────
#
# EXECUTED, not grepped. The first version of these asserted the substring
# "!= posixpath.normpath(root)" appeared in the endpoint — which stayed true when
# the guard was deleted, because a second, unrelated check contained the same
# text. Replacing the real guard with `if False:` passed all ten tests. Running
# the rule is the only version that cannot be fooled that way.

import types as _types


def _load_rule():
    src = SFTP.read_text()
    tree = ast.parse(src)
    picked = [n for n in tree.body if getattr(n, "name", None) in {"upload_target", "SftpPathDenied"}]
    assert len(picked) == 2, f"expected upload_target + SftpPathDenied, got {[getattr(n,'name',None) for n in picked]}"
    mod = _types.ModuleType("_upload_rule")
    mod.__dict__["posixpath"] = __import__("posixpath")
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<sftp>", "exec"), mod.__dict__)
    return mod


R = _load_rule()


def test_upload_lands_in_the_drop_point():
    assert R.upload_target("/tmp", "/tmp", "report.csv") == "/tmp/report.csv"


@pytest.mark.parametrize("base", ["/tmp/sub", "/tmp/a/b", "/etc", "/", "/tmp/nested"])
def test_upload_into_a_subdirectory_is_denied(base: str):
    """Browsing may descend; writing may not."""
    with pytest.raises(R.SftpPathDenied):
        R.upload_target("/tmp", base, "x.txt")


@pytest.mark.parametrize("filename", ["../evil", "../../etc/cron.d/x", "sub/evil", "/etc/passwd", "", ".", ".."])
def test_a_filename_with_separators_cannot_redirect_the_write(filename: str):
    """basename() strips separators, but relying on that silently is how it stops
    being true. Every one of these must land in the drop point or be refused."""
    try:
        out = R.upload_target("/tmp", "/tmp", filename)
    except R.SftpPathDenied:
        return
    assert out.rsplit("/", 1)[0] == "/tmp", f"{filename!r} escaped to {out}"
    assert ".." not in out


def test_destination_check_runs_after_symlink_resolution():
    """A symlink inside the root pointing at another in-root directory would
    otherwise widen the drop point back out to the whole tree."""
    body = _src("upload")
    resolve_at = body.index("await _resolve(sftp, path, root)")
    pin_at = body.index("upload_target(")
    assert resolve_at < pin_at, "the pin must follow resolution, not precede it"


# ── source: the request body, and nothing else ───────────────────────────────

_SOURCE_PARAM = re.compile(
    r"\b(url|uri|src|source|remote|remote_path|from_host|source_host|host_id|fetch|download_from)\b",
    re.I,
)


def test_upload_takes_no_source_other_than_the_uploaded_file():
    fn = _func("upload")
    params = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    offending = [p for p in params if _SOURCE_PARAM.search(p) and p != "session_id"]
    assert not offending, (
        f"upload accepts {offending}, which would let it pull bytes from somewhere "
        "other than the caller's browser — the PAM must not become a transfer agent "
        "between machines, or its audit rows name the wrong actor"
    )
    assert any(a.arg == "file" for a in fn.args.args + fn.args.kwonlyargs), \
        "the uploaded file must be the byte source"


def test_upload_reads_bytes_only_from_the_request_body():
    """The only .read() in the write loop must be the UploadFile's."""
    fn = _func("upload")
    reads = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "read"
    ]
    assert reads, "no read() found — the write loop did not parse"
    for call in reads:
        owner = call.func.value
        assert isinstance(owner, ast.Name) and owner.id == "file", (
            "upload reads bytes from something other than the request body"
        )


def test_no_endpoint_fetches_remote_content():
    """httpx is used in this module for identity lookups, which is fine. What must
    not exist is a fetch whose result is written to a managed host."""
    src = SFTP.read_text()
    for name in ("upload", "mkdir", "rename", "remove", "download", "list_dir"):
        body = _src(name)
        for banned in ("httpx.", "urllib", "AsyncClient", "requests."):
            assert banned not in body, f"{name} performs an HTTP fetch — a transfer path that is not the browser"


def test_there_is_no_host_to_host_copy_endpoint():
    """"Not from other servers" is only true while no endpoint takes two hosts."""
    src = SFTP.read_text()
    tree = ast.parse(src)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") in
                   {"get", "post", "put", "delete", "patch"} for d in node.decorator_list):
            continue
        params = [a.arg for a in node.args.args + node.args.kwonlyargs]
        session_params = [p for p in params if "session" in p.lower()]
        assert len(session_params) <= 1, (
            f"{node.name} names more than one session ({session_params}) — that is the "
            "shape of a host-to-host transfer, which must not exist here"
        )


# ── the client agrees ────────────────────────────────────────────────────────

def test_panel_uploads_to_the_drop_point_not_the_browsed_directory():
    """The server would refuse anything else; sending cwd would just surface as a
    confusing 403 once the user navigated into a subdirectory."""
    panel = PANEL.read_text()
    assert "fd.append('path', DEFAULT_PATH)" in panel, "the panel must upload to the root"
    assert "fd.append('path', cwd.value)" not in panel


def test_panel_uploads_a_locally_chosen_file():
    """<input type=file> — the browser's own picker, not a path the page composes."""
    panel = PANEL.read_text()
    assert 'type="file"' in panel
    assert "input.files?.[0]" in panel, "the byte source must be the user's file selection"
