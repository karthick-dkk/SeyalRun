"""SFTP enforces the grants that used to enforce nothing.

R-11: AuthorizationsAdmin has offered `sftp`, `upload` and `download` as
grantable actions since long before any of them was implemented. The grant was
stored and audited and enforced nothing, so an access review could report "user
X may download from host Y" while the product had no file transfer at all. On a
system whose purpose is proving who could do what, that is worse than a missing
feature.

Increment 1 makes them real. These pin the properties that make the claim true
rather than the fact that some SFTP code exists:

  * each action is checked separately — `sftp` browses and does NOT download;
  * the check resolves the SAME za_authorization record SSH uses, so there is
    one authorization model, not a second one that can drift;
  * refusals are audited, not just successes — the denied transfer is the row an
    assessor looks for;
  * transfers are critical=True, so a transfer that cannot be logged does not
    happen;
  * SFTP rides the session's existing SSH connection, so there is no second
    authentication path and no second credential unwrap.

Source-shape checks: the invariant suite has no SSH server and no database, and
every property here is structural.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TS = ROOT / "services/terminal-service"
SFTP = TS / "app/api/sftp.py"
REGISTRY = TS / "app/sftp_registry.py"
TERMINAL = TS / "app/ws/terminal.py"
MAIN = TS / "app/main.py"
AUTHZ_ADMIN = ROOT / "services/frontend/src/views/admin/AuthorizationsAdmin.vue"


def _fn(src: str, name: str) -> str:
    """Source of one top-level function, by AST span — not a brittle text slice."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


def _endpoints() -> dict[str, str]:
    """route function name -> its source, for every @router.<verb> handler."""
    src = SFTP.read_text()
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            # @router.get(...) / @router.post(...) / ...
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            owner = dec.func.value
            if isinstance(owner, ast.Name) and owner.id == "router":
                out[node.name] = ast.get_source_segment(src, node) or ""
    return out


def test_module_exists_and_endpoints_parse():
    """Guard against every assertion below passing vacuously."""
    assert SFTP.exists() and REGISTRY.exists()
    eps = _endpoints()
    assert {"list_dir", "download", "upload"} <= set(eps), f"parsed only: {sorted(eps)}"


def test_every_endpoint_authorizes_before_touching_the_filesystem():
    for name, body in _endpoints().items():
        assert "_authorize(" in body, f"{name} performs file I/O without an authorization check"
        auth_at = body.index("_authorize(")
        for io in ("start_sftp_client",):
            if io in body:
                assert body.index(io) > auth_at, f"{name} opens SFTP before authorizing"


@pytest.mark.parametrize("endpoint,action", [
    ("list_dir", "sftp"),
    ("download", "download"),
    ("upload", "upload"),
    ("mkdir", "sftp"),
    ("rename", "sftp"),
    ("remove", "sftp"),
])
def test_each_endpoint_requires_its_own_action(endpoint: str, action: str):
    """The distinction R-11 promised: `sftp` alone must not permit a download."""
    body = _endpoints()[endpoint]
    m = re.search(r'_authorize\(\s*session_id,\s*"([a-z]+)"', body)
    assert m, f"{endpoint} does not name an action in its _authorize call"
    assert m.group(1) == action, f"{endpoint} checks '{m.group(1)}', expected '{action}'"


def test_download_and_upload_do_not_accept_the_browse_grant():
    """Stated as its own assertion because it is the exact claim in the plan's
    exit criterion: a grant of sftp without download blocks a download."""
    for endpoint in ("download", "upload"):
        body = _endpoints()[endpoint]
        assert '_authorize(session_id, "sftp"' not in body, (
            f"{endpoint} accepts the browse grant — sftp must not imply transfer"
        )


def test_authorization_uses_the_same_record_ssh_uses():
    """A second authorization source would be a second thing to keep in sync."""
    body = _fn(SFTP.read_text(), "_authorize")
    assert "/internal/authz/resolve" in body, "must resolve the same za_authorization record as sessions.py"
    assert 'authz.get("actions"' in body
    # Same semantics as sessions.py: empty actions == no per-action restriction.
    assert "if actions and action not in actions" in body


def test_refusals_are_audited_not_only_successes():
    body = _fn(SFTP.read_text(), "_authorize")
    assert 'result="failure"' in body, "a denied file operation must produce an audit row"
    deny = body.index("_deny")
    assert "log_action" in body[deny - 400: deny + 600], "the denial path must log before raising"


def test_transfers_are_critical():
    """A transfer that cannot be written to the chain must not proceed."""
    for endpoint in ("download", "upload"):
        body = _endpoints()[endpoint]
        assert "critical=True" in body, f"{endpoint} must audit critically"


def test_download_audits_before_streaming_bytes():
    """Auditing after the stream would record only the transfers that succeeded,
    and none of the ones interrupted halfway — the opposite of what Req 10 wants."""
    body = _endpoints()["download"]
    assert "critical=True" in body and "StreamingResponse" in body
    assert body.index("critical=True") < body.index("StreamingResponse"), (
        "the audit row must be written before the response starts streaming"
    )


def test_sftp_rides_the_existing_connection():
    """No second authentication, no second credential unwrap, no rebuilt
    ProxyJump chain — the plan's explicit constraint."""
    body = SFTP.read_text()
    assert "sftp_registry.get(" in body
    assert "asyncssh.connect" not in body, "SFTP must not open its own SSH connection"
    for banned in ("/internal/credentials/", "import_private_key"):
        assert banned not in body, f"SFTP must not unwrap credentials itself ({banned})"


def test_connection_is_registered_and_always_released():
    term = TERMINAL.read_text()
    assert "sftp_registry.register(" in term, "the terminal must publish its connection"
    unreg = term.index("sftp_registry.unregister(")
    finally_at = term.rindex("finally:", 0, unreg)
    assert finally_at != -1, "unregister must run from a finally block, or a dead session keeps a live handle"


def test_upload_is_bounded():
    """An unbounded upload fills a managed host's disk from a grant that only
    meant 'put a config file there'."""
    body = SFTP.read_text()
    assert "MAX_UPLOAD_BYTES" in body
    assert "413" in body or "REQUEST_ENTITY_TOO_LARGE" in body


def test_router_is_mounted_and_service_token_guarded():
    assert "sftp_router" in MAIN.read_text(), "the SFTP router must be mounted"
    body = SFTP.read_text()
    assert "dependencies=[Depends(require_service_token)]" in body, (
        "every SFTP route must require the gateway's service token"
    )


def test_r11_is_retired_in_the_ui():
    """The actions were shown disabled and labelled while unimplemented. Now that
    they are enforced, leaving that label would be its own lie."""
    src = AUTHZ_ADMIN.read_text()
    m = re.search(r"const UNENFORCED_ACTIONS[^=]*=\s*(\[[^\]]*\])", src)
    assert m, "UNENFORCED_ACTIONS not found"
    still_flagged = re.findall(r"'([a-z]+)'", m.group(1))
    assert not still_flagged, f"still marked unenforced after Increment 1: {still_flagged}"
