"""SEYALRUN_ADMIN_APPROVAL: one backend switch for "another admin must approve".

Unit level: the parser in libs/adminapproval, every service reading it from its own
Settings, and identity-service's access-rule lifecycle run for real (the function is
lifted out of authorizations.py so no database is needed). The rollout, package,
facts, hook, job, schedule, webhook and chain gates are exercised end to end in the
opt-in DB suites (test_posture_m4_db.py, test_automation_host_ops_db.py) and in
test_rerun_approval.py.
"""

from __future__ import annotations

import ast
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from libs import adminapproval

CORE = Path(__file__).resolve().parent.parent
SERVICES = CORE / "services"


# ── the parser ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,required", [
    (None, False), ("off", False), ("OFF", False), (" off\n", False),
    ("on", True), ("On", True),
    # anything else is "on": fail safe
    ("", True), ("false", True), ("0", True), ("no", True), ("disabled", True), ("of", True),
])
def test_parse(value, required):
    assert adminapproval.approval_required(value) is required


def test_unknown_value_warns_once(caplog):
    adminapproval._warned.discard("maybe")
    with caplog.at_level(logging.WARNING, logger="libs.adminapproval"):
        for _ in range(3):
            assert adminapproval.approval_required("maybe")
    warnings = [r for r in caplog.records if "SEYALRUN_ADMIN_APPROVAL" in r.getMessage()]
    assert len(warnings) == 1 and "'maybe'" in warnings[0].getMessage()


def test_admin_roles_are_admin_and_superadmin_only():
    assert adminapproval.is_admin_role("admin") and adminapproval.is_admin_role("superadmin")
    for role in (None, "", "support", "user", "automation", "Admin"):
        assert not adminapproval.is_admin_role(role)


def test_waiver_names_the_switch_and_what_was_skipped():
    w = adminapproval.waiver(["production host", "production host", "template requires approval"])
    assert w == {"approval": "not required (SEYALRUN_ADMIN_APPROVAL=off)",
                 "skipped": ["production host", "template requires approval"]}


@pytest.mark.parametrize("svc", ["posture-service", "automation-service", "identity-service"])
def test_each_service_reads_it_from_its_own_settings_defaulting_off(svc):
    src = (SERVICES / svc / "app/config.py").read_text()
    assert 'seyalrun_admin_approval: str = "off"' in src
    assert 'env_file' in src


@pytest.mark.parametrize("svc", ["posture-service", "automation-service", "identity-service"])
def test_compose_hands_every_affected_service_the_env_file(svc):
    import yaml
    for compose in ("docker-compose.yml", "docker-compose.prod.yml"):
        spec = yaml.safe_load((CORE.parent / compose).read_text())["services"].get(svc)
        if spec is not None:
            assert spec.get("env_file") == ".env", (compose, svc)


def test_env_example_documents_the_switch():
    env = (CORE.parent / ".env.example").read_text()
    assert "\nSEYALRUN_ADMIN_APPROVAL=off\n" in env
    i = env.index("SEYALRUN_ADMIN_APPROVAL=off")
    assert abs(i - env.index("POSTURE_PATCH_APPROVAL=")) < 1200, "keep it next to POSTURE_PATCH_APPROVAL"


def test_no_service_parses_the_value_itself():
    """One parser. A copy that compares against "off" would disagree on case,
    whitespace or unknown values."""
    for f in SERVICES.rglob("*.py"):
        src = f.read_text()
        if "seyalrun_admin_approval" in src:
            assert '== "off"' not in src and '== "on"' not in src, f


# ── identity-service: access rules ───────────────────────────────────────────

class _HTTPError(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(detail)
        self.status_code = status_code


def _lifecycle():
    src = (SERVICES / "identity-service/app/api/authorizations.py").read_text()
    tree = ast.parse(src)
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_merge", "_apply_and_require_reapproval")]
    assert len(keep) == 2
    for n in keep:
        for a in [*n.args.args, *n.args.kwonlyargs]:
            a.annotation = None
        n.returns = None
    g = {"HTTPException": _HTTPError, "status": NS(HTTP_400_BAD_REQUEST=400), "datetime": datetime,
         "timedelta": timedelta, "timezone": timezone, "NOT_REQUIRED": adminapproval.NOT_REQUIRED,
         "is_admin_role": adminapproval.is_admin_role}
    exec(compile(ast.Module(keep, []), "authorizations", "exec"), g)
    return g["_apply_and_require_reapproval"]


def _payload():
    return NS(name="r", user_ids=["u"], user_id=None, user_group_ids=[], user_group_id=None, host_ids=["h"], host_id=None,
              host_group_ids=[], host_group_id=None, credential_ids=[], credential_id=None, actions=["connect"],
              date_start=None, date_expired=None)


def _apply(role, *, admin_approval, exempt=("superadmin",)):
    authz = NS()
    note = _lifecycle()(authz, _payload(), 90, "actor-1", role, list(exempt), admin_approval)
    return authz, note


@pytest.mark.parametrize("role", ["admin", "superadmin"])
def test_off_admin_rule_is_active_at_once(role):
    authz, note = _apply(role, admin_approval=False, exempt=())
    assert authz.status == "active" and authz.enabled is True
    assert authz.requested_by == authz.approved_by == "actor-1" and authz.approved_at is not None
    assert note == adminapproval.NOT_REQUIRED, "the audit row must say why no second admin approved it"


@pytest.mark.parametrize("role", ["support", "user", None, ""])
def test_off_non_admin_rule_still_waits(role):
    """A non-admin's request (including the user "request access" flow) always needs an admin."""
    authz, note = _apply(role, admin_approval=False, exempt=())
    assert authz.status == "pending_approval" and authz.enabled is False and authz.approved_by is None
    assert note is None


def test_on_admin_rule_waits_as_before():
    authz, note = _apply("admin", admin_approval=True)
    assert authz.status == "pending_approval" and authz.enabled is False and note is None


def test_exempt_role_is_not_credited_to_the_switch():
    authz, note = _apply("superadmin", admin_approval=False)
    assert authz.status == "active" and note is None
    authz, note = _apply("superadmin", admin_approval=True)
    assert authz.status == "active" and note is None


def test_an_edit_re_opens_approval_unless_off_and_admin():
    apply = _lifecycle()
    authz = NS()
    apply(authz, _payload(), 90, "a", "admin", [], False)
    assert authz.status == "active"
    apply(authz, _payload(), 90, "s", "support", [], False)
    assert authz.status == "pending_approval" and authz.enabled is False


def test_identity_endpoints_pass_the_switch_and_audit_the_note():
    src = (SERVICES / "identity-service/app/api/authorizations.py").read_text()
    for route in ("async def create_authorization", "async def update_authorization"):
        body = src.split(route, 1)[1].split("@router", 1)[0]
        assert "_admin_approval_required())" in body, route
        assert '**({"approval": waived} if waived else {})' in body, route
        # actor_role is the REAL role, never the write-vouched X-User-Role
        assert 'actor_role: str | None = Header(default=None, alias="X-User-Real-Role")' in body, route


def test_approve_and_reject_are_untouched_in_both_modes():
    """Anything already pending keeps working with the existing endpoints."""
    src = (SERVICES / "identity-service/app/api/authorizations.py").read_text()
    approve = src.split("async def approve_authorization", 1)[1].split("@router", 1)[0]
    assert "seyalrun_admin_approval" not in approve and "_admin_approval_required" not in approve
    assert "the requester cannot approve their own authorization" in approve


# ── identity-service: who counts as an admin for automation-service ──────────

def test_roles_endpoint_reports_a_deactivated_user_as_inactive(monkeypatch):
    """automation-service's switch check requires is_active=True: a deactivated admin's
    roles still say "admin", so without the flag their schedules would skip approval."""
    import asyncio
    import sys
    import types

    src = (SERVICES / "identity-service/app/api/internal.py").read_text()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "internal_user_roles")
    fn.decorator_list, fn.args.defaults, fn.returns = [], [], None
    for a in fn.args.args:
        a.annotation = None

    async def effective_roles(session, uid):
        return ["admin"]
    for name, mod in (("identpkg", types.ModuleType("identpkg")), ("identpkg.api", types.ModuleType("identpkg.api")),
                      ("identpkg.rbacresolve", NS(effective_roles=effective_roles))):
        monkeypatch.setitem(sys.modules, name, mod)
    users = {"on": NS(is_active=True), "off": NS(is_active=False)}

    class Session:
        async def get(self, model, uid):
            return users.get(uid)
    g = {"ZAUser": object, "__package__": "identpkg.api", "__name__": "identpkg.api.internal"}
    exec(compile(ast.Module([fn], []), "internal", "exec"), g)
    call = g["internal_user_roles"]
    assert asyncio.run(call("on", Session())) == {"roles": ["admin"], "known": True, "is_active": True}
    assert asyncio.run(call("off", Session())) == {"roles": ["admin"], "known": True, "is_active": False}
    assert asyncio.run(call("ghost", Session()))["is_active"] is False
