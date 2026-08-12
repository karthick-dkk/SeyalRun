"""RBAC v2 core: the pure union/enforcement logic that the whole auth boundary rests on.
libs.rbaccore is stdlib-only and pure, so it's exercised exhaustively without a DB —
the effective-role union, per-verb-per-segment enforcement, the reveal flag, and the
mandatory-authorization bypass set (only superadmin/admin)."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from libs import rbaccore as rc  # noqa: E402


class TestMergeRoles:
    def test_union_dedup(self):
        assert rc.merge_roles(["user"], ["admin", "user"]) == ["admin", "user"]

    def test_empty(self):
        assert rc.merge_roles([], []) == []
        assert rc.merge_roles(None, None) == []

    def test_group_only(self):
        assert rc.merge_roles([], ["support"]) == ["support"]

    def test_direct_only(self):
        assert rc.merge_roles(["admin"], []) == ["admin"]

    def test_two_groups_overlap(self):
        # user in two groups both granting 'support', plus direct 'user'
        assert rc.merge_roles(["user"], ["support", "support"]) == ["support", "user"]

    def test_precedence_ordering(self):
        # highest-privilege first regardless of input order
        assert rc.merge_roles(["user"], ["superadmin", "audit"])[0] == "superadmin"

    def test_custom_role_appended(self):
        assert rc.merge_roles(["custom-x"], ["user"]) == ["user", "custom-x"]


class TestRoleAllows:
    def test_superadmin_all(self):
        assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["superadmin"], "DELETE", "anything") is True

    def test_per_verb_read_yes_write_no(self):
        # user role: hosts is GET-only
        assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["user"], "GET", "hosts") is True
        assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["user"], "POST", "hosts") is False

    def test_ssh_capability(self):
        assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["user"], "POST", "ssh") is True

    def test_none_and_unknown_segment_deny(self):
        assert rc.role_allows(None, "GET", "hosts") is False
        assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["user"], "GET", "unknown-seg") is False


class TestRevealFlag:
    def test_admin_reveals(self):
        assert rc.doc_can_reveal(rc.BUILTIN_ROLE_PERMS["admin"]) is True

    def test_user_cannot_reveal(self):
        assert rc.doc_can_reveal(rc.BUILTIN_ROLE_PERMS["user"]) is False


class TestBypassAuthz:
    """Mandatory-authorization gate: ONLY superadmin/admin bypass. Every other role —
    including a role WITH the ssh capability — is default-deny and needs a grant."""

    def test_superadmin_and_admin_bypass(self):
        assert rc.bypasses_authz(["superadmin"]) is True
        assert rc.bypasses_authz(["admin", "user"]) is True

    def test_non_admin_roles_do_not_bypass(self):
        for r in ("support", "automation", "audit", "guest", "user", "some-custom-role"):
            assert rc.bypasses_authz([r]) is False, r

    def test_role_with_ssh_capability_still_needs_grant(self):
        # 'user' HAS ssh capability (role_allows POST ssh == True) but does NOT bypass —
        # this is the crux: capability is necessary, not sufficient.
        assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["user"], "POST", "ssh") is True
        assert rc.bypasses_authz(["user"]) is False


class TestZoneCapability:
    """Zone/gateway management (ProxyJump chain topology) is admin-only — support
    keeps the rest of inventory CRUD but loses zones specifically."""

    def test_admin_manages_zones(self):
        assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["admin"], "PUT", "zones") is True

    def test_support_cannot_manage_zones(self):
        assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["support"], "GET", "zones") is False
        assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["support"], "PUT", "zones") is False

    def test_support_keeps_other_inventory_segments(self):
        for seg in ("hosts", "host-groups", "credentials", "credential-templates"):
            assert rc.role_allows(rc.BUILTIN_ROLE_PERMS["support"], "GET", seg) is True
