"""The file manager is confined to the SFTP root, and escapes are audited.

I shipped this feature with a containment helper that contained nothing. Its
docstring said it rejected paths climbing out of the root; the body normalised
the string and returned it, so an absolute path passed straight through. The
commit message repeated the claim. The runtime proof for the feature downloaded
/etc/hostname and I read that as success.

What it meant: any account granted `download` on a host could read every file
that account could read — /etc/shadow, private keys, application secrets —
through a file browser, while the grant looked like nothing more than "may
fetch files". A PAM whose file manager is an arbitrary-read primitive is worse
than one with no file manager, because the grant misdescribes it.

Matches JumpServer PAM, whose per-asset SFTP Root also defaults to /tmp.

These tests EXECUTE the containment logic rather than asserting it exists —
which is the specific way the original slipped through, and the same lesson as
the history cap earlier: a test that greps for a fix cannot tell you it runs.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SFTP_SRC = ROOT / "services/terminal-service/app/api/sftp.py"


def _load_helpers():
    """Execute just the containment helpers, with no FastAPI/app imports.

    The module cannot be imported whole here (it pulls in the service), so the
    two functions under test are compiled from their own source. They are pure
    apart from the injected sftp client, which is what makes this possible.
    """
    src = SFTP_SRC.read_text()
    tree = ast.parse(src)
    wanted = {"_within_root", "_resolve", "SftpPathDenied"}
    picked = [n for n in tree.body
              if getattr(n, "name", None) in wanted]
    assert len(picked) == 3, f"expected 3 helpers, found {[getattr(n,'name',None) for n in picked]}"
    mod = types.ModuleType("_sftp_helpers")
    mod.__dict__["posixpath"] = __import__("posixpath")
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<sftp>", "exec"), mod.__dict__)
    return mod


H = _load_helpers()


class FakeSftp:
    """Minimal stand-in. `links` maps a path to what realpath resolves it to,
    which is how a symlink escape is modelled."""
    def __init__(self, links: dict[str, str] | None = None, missing: set[str] | None = None):
        self.links = links or {}
        self.missing = missing or set()

    async def realpath(self, p: str) -> str:
        import posixpath
        p = posixpath.normpath(p)
        if p in self.missing:
            raise FileNotFoundError(p)
        for src, dst in self.links.items():
            if p == src or p.startswith(src.rstrip("/") + "/"):
                return posixpath.normpath(p.replace(src, dst, 1))
        return p


def resolve(path, root="/tmp", links=None, missing=None):
    # asyncio.run, not get_event_loop(): once any other test in the session has
    # called asyncio.run(), the loop get_event_loop() hands back is closed, and
    # these fail with RuntimeError only when run as part of the full suite —
    # passing in isolation, which is the worst way to find out.
    return asyncio.run(H._resolve(FakeSftp(links, missing), path, root))


# ── the containment predicate ────────────────────────────────────────────────

@pytest.mark.parametrize("resolved,root,expected", [
    ("/tmp", "/tmp", True),
    ("/tmp/a/b", "/tmp", True),
    ("/etc/shadow", "/tmp", False),
    ("/", "/tmp", False),
    # startswith() would wrongly accept this — the exact bug a prefix check has.
    ("/tmpevil/x", "/tmp", False),
    ("/tmp2", "/tmp", False),
    # root="/" disables confinement, deliberately and visibly.
    ("/etc/shadow", "/", True),
])
def test_within_root(resolved: str, root: str, expected: bool):
    assert H._within_root(resolved, root) is expected


# ── resolution + confinement together ────────────────────────────────────────

def test_relative_paths_resolve_under_the_root():
    assert resolve("sub/file") == "/tmp/sub/file"
    assert resolve(".") == "/tmp"


def test_absolute_path_outside_the_root_is_denied():
    """The original hole: an absolute path bypassed the helper entirely."""
    with pytest.raises(H.SftpPathDenied):
        resolve("/etc/shadow")
    with pytest.raises(H.SftpPathDenied):
        resolve("/etc/hostname")   # exactly what the first runtime proof read


def test_dotdot_escape_is_denied():
    with pytest.raises(H.SftpPathDenied):
        resolve("../etc/passwd")
    with pytest.raises(H.SftpPathDenied):
        resolve("a/../../root/.ssh/id_rsa")


def test_symlink_escape_is_denied():
    """The reason resolution must be server-side and BEFORE the check: a string
    check would accept /tmp/escape while it points at /etc — and any user who can
    write to /tmp can plant that link."""
    with pytest.raises(H.SftpPathDenied):
        resolve("/tmp/escape/shadow", links={"/tmp/escape": "/etc"})


def test_symlink_inside_the_root_is_allowed():
    """Confinement must not break ordinary use — a link that stays inside is fine."""
    assert resolve("/tmp/link/f", links={"/tmp/link": "/tmp/real"}) == "/tmp/real/f"


def test_nonexistent_leaf_resolves_via_its_parent():
    """Upload targets and mkdir names do not exist yet; realpath on them fails.
    Resolving the parent must still confine the result."""
    assert resolve("/tmp/newfile", missing={"/tmp/newfile"}) == "/tmp/newfile"
    with pytest.raises(H.SftpPathDenied):
        resolve("/etc/newfile", missing={"/etc/newfile"})


def test_root_slash_allows_everything():
    """Escape hatch for deployments that want no confinement — it must work, and
    it must require setting the value to '/' rather than happening by accident."""
    assert resolve("/etc/shadow", root="/") == "/etc/shadow"


# ── wiring: no operation may bypass it ───────────────────────────────────────

def test_no_endpoint_resolves_paths_without_confinement():
    src = SFTP_SRC.read_text()
    assert "_safe_join" not in src, "the non-enforcing helper must be gone"
    # realpath is legitimate INSIDE _resolve (it is how resolution happens);
    # anywhere else it is an endpoint resolving a path with no confinement check.
    tree = ast.parse(src)
    resolve_span = next(
        (n.lineno, n.end_lineno) for n in tree.body
        if getattr(n, "name", None) == "_resolve"
    )
    outside = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "realpath" and not (resolve_span[0] <= n.lineno <= resolve_span[1])
    ]
    assert not outside, (
        f"realpath called outside _resolve at line(s) {outside} — those paths are "
        "never confined to the SFTP root"
    )
    assert src.count("await _resolve(") >= 6, "every operation must resolve through _resolve"


def test_path_denials_are_audited_as_refusals():
    src = SFTP_SRC.read_text()
    fn = src[src.index("async def _deny_path"):]
    fn = fn[: fn.index("\n@router")]
    assert 'result="failure"' in fn
    assert "critical=True" in fn, "an attempted escape is exactly what must not go unlogged"
    assert "HTTP_403_FORBIDDEN" in fn, "an escape is an access-control refusal, not a bad request"
    # every endpoint converts the exception rather than letting it 500
    assert src.count("except SftpPathDenied as denied:") >= 6


def test_root_is_configurable_and_defaults_to_tmp():
    cfg = (ROOT / "services/terminal-service/app/config.py").read_text()
    m = re.search(r'sftp_root:\s*str\s*=\s*"([^"]+)"', cfg)
    assert m and m.group(1) == "/tmp", f"sftp_root default is {m and m.group(1)!r}"
