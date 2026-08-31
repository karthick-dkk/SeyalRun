"""JumpServer IdP plugin: the privilege mapping (executed) and the properties that
keep a delegated-identity plugin safe (TLS on, RBAC-role sync, disabled by default).

The full authenticate() path talks to JumpServer + the DB and is exercised on a
deployment that actually fronts a JumpServer; here we pin the parts that must hold
regardless: which SeyalRun role a JumpServer profile maps to, that verification is
never turned off, and that an auto-provisioned user gets a real za_user_roles row
(without it the RBAC layer grants nothing).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "services/identity-service/app/plugins/idp/jumpserver.py"


def _load_role_for_profile():
    """Exec just the pure role_for_profile function — no httpx/sqlalchemy/app imports."""
    src = PLUGIN.read_text()
    start = src.index("def role_for_profile")
    rest = src[start:]
    m = re.search(r"\n(?=class |def )", rest[len("def role_for_profile"):])
    body = rest[: m.start() + len("def role_for_profile")] if m else rest
    ns: dict = {}
    exec(compile(body, str(PLUGIN), "exec"), ns)  # noqa: S102 - one pure function from our own source
    return ns["role_for_profile"]


role_for_profile = _load_role_for_profile()


def test_superuser_maps_to_superadmin():
    assert role_for_profile({"username": "root", "is_superuser": True}) == "superadmin"


def test_org_admin_and_admin_role_map_to_admin():
    assert role_for_profile({"username": "a", "is_org_admin": True}) == "admin"
    assert role_for_profile({"username": "b", "system_roles": [{"name": "SystemAdmin"}]}) == "admin"
    assert role_for_profile({"username": "c", "roles": ["Admin"]}) == "admin"


def test_plain_user_maps_to_user():
    assert role_for_profile({"username": "d"}) == "user"
    assert role_for_profile({"username": "e", "system_roles": [{"name": "User"}]}) == "user"


# ── security properties (source-pinned) ──────────────────────────────────────

def test_verification_is_never_disabled():
    src = PLUGIN.read_text()
    assert "verify=False" not in src, "TLS verification must never be turned off"
    # verify defaults to True and only takes a CA bundle
    assert "settings.jumpserver_ca_bundle or True" in src


def test_disabled_by_default_when_no_url():
    src = PLUGIN.read_text()
    fn = src[src.index("async def _fetch_profile"):]
    assert "if not base or not token:" in fn and "return None" in fn


def test_provisions_a_real_rbac_role():
    """RBAC reads za_user_roles, not the legacy role_id — a delegated user must get
    an actual role link or every request 403s (the exact bug zabbix_sso documents)."""
    src = PLUGIN.read_text()
    assert "ZAUserRole(user_id=user.id, role_id=role.id)" in src


def test_it_is_a_named_identity_provider():
    src = PLUGIN.read_text()
    assert "class JumpServerProvider(IdentityProvider)" in src
    assert 'name = "jumpserver"' in src
