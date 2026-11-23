"""Dashboard "Your access": the caller's effective access rules — direct AND
group-granted — via GET /api/v1/auth/my-access, and nobody else's.

The selection (identity-service/app/myaccess.py) is executed against rule
objects; the endpoint's only notion of "who" is the gateway-set X-User-Id.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from libs.apiscopes import required_scope, scope_allows

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
ME, OTHER = "user-me", "user-other"
GROUPS = {"g-ops": "Ops on-call", "g-dba": "DBAs"}   # groups ME belongs to


def rule(rid, **kw):
    base = dict(id=rid, name=rid, user_id=None, user_ids=[], user_group_id=None, user_group_ids=[],
                host_id=None, host_ids=["h1"], host_group_id=None, host_group_ids=[],
                credential_id="cred-x", credential_ids=["cred-y"], actions=["ssh"],
                date_start=None, date_expired=None, enabled=True, status="active")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def rows(load_module):
    mod = load_module("services/identity-service/app/myaccess.py", "id_myaccess")

    def run(rules, user_id=ME, groups=GROUPS):
        return mod.effective_access_rows(rules, user_id, groups, NOW)
    return run


def test_direct_and_group_rules_both_listed_with_group_names(rows):
    out = rows([
        rule("direct", user_ids=[ME]),
        rule("legacy-direct", user_id=ME),
        rule("via-group", user_group_ids=["g-ops", "g-elsewhere"]),
        rule("via-two-groups", user_group_id="g-dba", user_group_ids=["g-ops"]),
    ])
    by = {r["id"]: r for r in out}
    assert set(by) == {"direct", "legacy-direct", "via-group", "via-two-groups"}
    assert by["direct"]["via"] == "direct" and by["direct"]["groups"] == []
    assert by["via-group"]["via"] == "group" and by["via-group"]["groups"] == ["Ops on-call"]
    assert by["via-two-groups"]["groups"] == ["DBAs", "Ops on-call"]
    # direct rules first, then group rules
    assert [r["via"] for r in out] == ["direct", "direct", "group", "group"]


def test_direct_wins_label_when_both_apply(rows):
    (r,) = rows([rule("both", user_ids=[ME], user_group_ids=["g-ops"])])
    assert r["via"] == "direct" and r["groups"] == ["Ops on-call"]


def test_another_users_rules_are_not_returned(rows):
    out = rows([
        rule("theirs", user_ids=[OTHER]),
        rule("their-group", user_group_ids=["g-not-mine"]),
        rule("mine", user_ids=[ME]),
    ])
    assert [r["id"] for r in out] == ["mine"]
    # and the same rules seen as OTHER give OTHER's view, not mine
    both = [rule("theirs", user_ids=[OTHER]), rule("mine", user_ids=[ME])]
    assert [r["id"] for r in rows(both, user_id=OTHER, groups={})] == ["theirs"]


def test_rows_never_carry_other_principals_or_credentials(rows):
    (r,) = rows([rule("shared", user_ids=[ME, OTHER], user_group_ids=["g-ops", "g-secret"])])
    flat = repr(r)
    assert OTHER not in flat and "g-secret" not in flat and "cred-" not in flat
    assert set(r) == {"id", "name", "actions", "host_ids", "host_group_ids", "date_start",
                      "date_expired", "status", "via", "groups"}


def test_inactive_rules_are_excluded_pending_kept(rows):
    out = rows([
        rule("expired", user_ids=[ME], date_expired=NOW - timedelta(hours=1)),
        rule("rejected", user_ids=[ME], status="rejected", enabled=False),
        rule("disabled", user_ids=[ME], enabled=False),
        rule("pending", user_group_ids=["g-ops"], status="pending_approval", enabled=False),
        rule("ends-later", user_ids=[ME], date_expired=NOW + timedelta(days=2)),
    ])
    assert {r["id"] for r in out} == {"pending", "ends-later"}


def test_targets_fold_legacy_scalars(rows):
    (r,) = rows([rule("t", user_ids=[ME], host_id="h0", host_ids=["h1"], host_group_id="hg1")])
    assert r["host_ids"] == ["h1", "h0"] and r["host_group_ids"] == ["hg1"]


# ── the endpoint: self-scoped by construction ────────────────────────────────

def _endpoint():
    src = (ROOT / "services/identity-service/app/api/my_access.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "my_access")
    return src, fn


def test_endpoint_takes_the_user_only_from_the_session():
    src, fn = _endpoint()
    params = {a.arg: ast.unparse(d) for a, d in zip(fn.args.args, fn.args.defaults)}
    assert params == {"user_id": "Depends(current_user_id)", "session": "Depends(get_session)"}
    assert "Query(" not in src and "Body(" not in src
    assert '@router.get("/my-access")' in src and 'prefix="/auth"' in src


def test_current_user_id_is_the_gateway_set_header():
    deps = (ROOT / "services/identity-service/app/deps.py").read_text()
    assert 'async def current_user_id(x_user_id: str | None = Header(default=None))' in deps
    # and the gateway strips any client-supplied X-User-Id before setting its own
    proxy = (ROOT / "services/api-gateway/app/proxy.py").read_text()
    assert '"x-user-id"' in proxy.split("_IDENTITY_HEADERS", 1)[1].split("}", 1)[0]


def test_router_is_mounted():
    main = (ROOT / "services/identity-service/app/main.py").read_text()
    assert "app.include_router(my_access.router, prefix=\"/api/v1\")" in main


def test_every_role_may_call_it_but_not_the_admin_rule_list(load_module):
    rbac = load_module("services/api-gateway/app/rbac.py", "gw_rbac_myaccess")
    for role in ("user", "support", "admin", "superadmin"):
        assert rbac.is_authorized([role], "GET", "auth/my-access"), role
    # the plain user still cannot list every rule in the system
    assert not rbac.is_authorized(["user"], "GET", "authorizations")


def test_agent_tokens_cannot_read_their_owners_grants():
    assert required_scope("auth", "GET", "auth/my-access") is None
    from libs.apiscopes import AGENT_GRANTABLE
    assert not scope_allows(sorted(AGENT_GRANTABLE), "auth", "GET", "auth/my-access")
