"""Host tree, view-only tree membership and self-service access requests —
the properties that hold without a database.

  * the tree stays a tree: no cycles, at most MAX_DEPTH levels, and expansion
    terminates even on a corrupted table;
  * a grant on a group covers its descendants, because the ONE membership
    endpoint every authorization caller goes through expands them;
  * view membership is opt-in visibility: only visibility consumers ask for it,
    no login-type consumer can receive it, and resolve never reads it;
  * a user's access request is always pending, always decided by someone else,
    and its window starts at approval.

The behaviour end to end (real PostgreSQL, real endpoints) is in
test_host_tree_db.py; this file is the part that runs in every suite.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
SVC = ROOT / "services"
INV_HOSTS = SVC / "inventory-service/app/api/hosts.py"
ID_INTERNAL = SVC / "identity-service/app/api/internal.py"
ID_AUTHZ = SVC / "identity-service/app/api/authorizations.py"
ID_REQ = SVC / "identity-service/app/api/access_requests.py"


@pytest.fixture
def tree(load_module):
    return load_module("services/inventory-service/app/grouptree.py", "grouptree_under_test")


@pytest.fixture
def ar(load_module):
    pytest.importorskip("fastapi")
    return load_module("services/identity-service/app/accessrequest.py", "accessrequest_under_test")


def _fn(path: Path, name: str) -> str:
    src = path.read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found in {path.name}")


# ── the tree ─────────────────────────────────────────────────────────────────

CHAIN = {"a": None, "b": "a", "c": "b", "x": None}


def test_descendants_cover_the_whole_subtree(tree):
    assert tree.descendants(CHAIN, ["a"]) == {"a", "b", "c"}
    assert tree.descendants(CHAIN, ["b"]) == {"b", "c"}
    assert tree.descendants(CHAIN, ["x"]) == {"x"}


def test_descendants_terminate_on_a_cycle(tree):
    """A cycle written behind the API's back must not hang the connect path."""
    assert tree.descendants({"a": "b", "b": "a"}, ["a"]) == {"a", "b"}
    assert tree.depth_of({"a": "b", "b": "a"}, "a") == 2


def test_parent_cannot_be_self_or_a_descendant(tree):
    assert "own parent" in tree.parent_error(CHAIN, "a", "a")
    assert "cycle" in tree.parent_error(CHAIN, "a", "c")
    assert "cycle" in tree.parent_error(CHAIN, "a", "b")
    assert tree.parent_error(CHAIN, "x", "c") is None
    assert tree.parent_error(CHAIN, "c", None) is None
    assert tree.parent_error(CHAIN, None, "missing") == "parent group not found"


def test_depth_limit_counts_the_moved_subtree(tree):
    assert tree.MAX_DEPTH == 8
    chain = {f"g{i}": (f"g{i - 1}" if i else None) for i in range(8)}   # g0..g7: 8 levels
    assert tree.depth_of(chain, "g7") == 8
    assert "levels deep" in tree.parent_error(chain, None, "g7"), "a 9th level must be refused"
    assert tree.parent_error(chain, None, "g6") is None, "the 8th level is allowed"
    # Moving a 2-level subtree under level 7 makes 9 levels.
    sub = {**chain, "s": None, "s1": "s"}
    assert "levels deep" in tree.parent_error(sub, "s", "g6")
    assert tree.parent_error(sub, "s", "g5") is None


def test_membership_endpoint_expands_descendants():
    """Every authorization caller reaches group membership through this one
    endpoint (identity's _hosts_in_groups), so this is where the tree has to apply."""
    body = _fn(INV_HOSTS, "internal_host_group_members")
    assert "descendants(await _group_parents(session), ids)" in body
    assert "ZAHostGroupMember.group_id.in_(sorted(expanded))" in body


def test_group_parents_is_one_query():
    body = _fn(INV_HOSTS, "_group_parents")
    assert body.count("session.execute(") == 1


def test_create_and_update_validate_the_parent():
    assert "parent_error(" in _fn(INV_HOSTS, "create_host_group")
    assert "parent_error(" in _fn(INV_HOSTS, "update_host_group")


def test_delete_refuses_a_group_with_children():
    body = _fn(INV_HOSTS, "delete_host_group")
    assert "ZAHostGroup.parent_id == group_id" in body
    assert "HTTP_409_CONFLICT" in body
    assert body.index("HTTP_409_CONFLICT") < body.index("session.delete(group)")


def test_host_accounts_never_carry_a_secret():
    body = _fn(INV_HOSTS, "internal_host_accounts")
    code = body.split('"""')[-1]          # after the docstring
    assert "secret" not in code.lower() and "decrypt" not in code.lower()
    assert "select(ZACredential.id, ZACredential.name, ZACredential.username)" in code


# ── view membership is visibility, never access ──────────────────────────────

LOGIN_CONSUMERS = {
    "terminal session create / connectable hosts": SVC / "terminal-service/app/api/sessions.py",
    "sftp": SVC / "terminal-service/app/api/sftp.py",
    "credential reveal": SVC / "inventory-service/app/api/credentials.py",
    "recordings": SVC / "recording-service/app/api/recordings.py",
}
VISIBILITY_CONSUMERS = {
    "gateway host list": SVC / "api-gateway/app/proxy.py",
    "posture": SVC / "posture-service/app/clients.py",
}


@pytest.mark.parametrize("name", sorted(LOGIN_CONSUMERS))
def test_login_consumers_never_ask_for_view_grants(name):
    src = LOGIN_CONSUMERS[name].read_text()
    assert "include_view" not in src, f"{name} must not receive view-only hosts"


@pytest.mark.parametrize("name", sorted(VISIBILITY_CONSUMERS))
def test_visibility_consumers_opt_in(name):
    assert '"include_view": "true"' in VISIBILITY_CONSUMERS[name].read_text()


def test_view_grants_are_opt_in_and_never_strict():
    body = _fn(ID_INTERNAL, "authz_host_ids")
    assert "include_view: bool = Query(default=False)" in body
    assert "if include_view and not strict:" in body


def test_resolve_never_reads_view_membership():
    """resolve is what picks a credential and the allowed actions for login, SFTP
    and reveal; it must only ever see za_authorizations."""
    body = _fn(ID_INTERNAL, "authz_resolve")
    assert "ZAHostGroupViewer" not in body and "_viewer_host_groups" not in body
    assert "select(ZAAuthorization)" in body


def test_view_membership_is_not_an_authorization_row():
    """Its own table with no access-shaped column, so nothing can read it as one."""
    tree_ = ast.parse((SVC / "identity-service/app/models.py").read_text())
    cls = next(n for n in tree_.body if isinstance(n, ast.ClassDef) and n.name == "ZAHostGroupViewer")
    cols = {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    assert {"host_group_id", "user_id", "user_group_id"} <= cols
    assert not cols & {"actions", "credential_id", "credential_ids", "enabled", "status"}


def test_sftp_refuses_when_no_rule_matches():
    """An expired request leaves an open session whose resolve is empty; empty
    actions alone must not read as unrestricted."""
    body = _fn(SVC / "terminal-service/app/api/sftp.py", "_authorize")
    assert "if not granted and not elevated:" in body
    assert body.index("if not granted and not elevated:") < body.index("if actions and action not in actions")


# ── access requests ──────────────────────────────────────────────────────────

def _req(**kw):
    base = dict(request_kind="user_request", requested_by="alice", user_id="alice", user_ids=["alice"],
                request_duration_minutes=60, status="pending_approval", date_start=None, date_expired=None)
    return SimpleNamespace(**{**base, **kw})


def test_requester_and_beneficiary_cannot_decide(ar):
    from fastapi import HTTPException

    for who in ("alice", None, ""):
        with pytest.raises(HTTPException) as e:
            ar.guard_decision(_req(), who)
        assert e.value.status_code == 403
    with pytest.raises(HTTPException):
        ar.guard_decision(_req(requested_by="carol", user_ids=["alice"], user_id=None), "alice")
    ar.guard_decision(_req(), "bob")                               # someone else: fine
    ar.guard_decision(_req(request_kind=None), "alice")            # admin rules: not this check's business


def test_window_starts_at_approval(ar):
    now = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    a = _req(request_duration_minutes=480, date_start=now - timedelta(days=3), date_expired=now - timedelta(days=2))
    ar.start_window(a, now)
    assert (a.date_start, a.date_expired) == (now, now + timedelta(hours=8))
    admin_rule = _req(request_kind=None, date_expired="untouched")
    ar.start_window(admin_rule, now)
    assert admin_rule.date_expired == "untouched"


def test_request_without_duration_is_not_open_ended(ar):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        ar.start_window(_req(request_duration_minutes=None), datetime.now(timezone.utc))


def test_only_the_documented_durations(ar):
    assert {k: m for k, (m, _) in ar.DURATIONS.items()} == {"1h": 60, "8h": 480, "1d": 1440, "7d": 10080}
    assert ar.REQUEST_ACTIONS == ["ssh"], "a request grants a login, not sftp/reveal"


def test_display_status_marks_lapsed_grants_expired(ar):
    now = datetime.now(timezone.utc)
    assert ar.display_status(_req(status="active", date_expired=now - timedelta(seconds=1)), now) == "expired"
    assert ar.display_status(_req(status="active", date_expired=now + timedelta(hours=1)), now) == "active"


def test_approve_paths_apply_both_request_rules():
    for fn in ("approve_authorization", "reject_authorization"):
        assert "guard_decision(authz, actor_id)" in _fn(ID_AUTHZ, fn)
    assert "start_window(authz, now)" in _fn(ID_AUTHZ, "approve_authorization")
    assert "start_window(authz, now)" in _fn(ID_AUTHZ, "authorization_email_action_confirm")
    assert "guard_decision(authz, approver.id)" in _fn(ID_AUTHZ, "_validate_email_token")
    assert "USER_REQUEST" in _fn(ID_AUTHZ, "update_authorization"), "requests are decided, not edited"


def test_requests_are_always_pending_whatever_the_policy():
    """Separate from the admin approval policy: no exempt-role lookup, no switch."""
    body = _fn(ID_REQ, "create_request")
    assert 'status="pending_approval", enabled=False' in body
    assert "requested_by=user_id" in body
    for policy in ("approval_exempt_roles", "_platform_approval_settings", "SEYALRUN_ADMIN_APPROVAL", "_apply_and_require_reapproval"):
        assert policy not in body


def test_request_endpoints_never_trust_a_role_header():
    """POSTs under /auth arrive with X-User-Role vouched up to admin."""
    src = ID_REQ.read_text()
    assert 'alias="X-User-Role"' not in src and 'alias="X-User-Real-Role"' not in src
