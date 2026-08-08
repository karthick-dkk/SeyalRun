"""Gateway RBAC matrix invariants (built-in role fallback, used until the live
role-perms cache populates). Pure function — no app/DB needed."""

from __future__ import annotations

import pytest


@pytest.fixture
def rbac(load_module):
    return load_module("services/api-gateway/app/rbac.py", "gw_rbac")


def test_superadmin_can_do_anything(rbac):
    assert rbac.is_authorized(["superadmin"], "DELETE", "users") is True


def test_plain_user_has_no_management_access(rbac):
    assert rbac.is_authorized(["user"], "GET", "users") is False
    assert rbac.is_authorized(["user"], "POST", "hosts") is False


def test_auth_segment_always_allowed(rbac):
    # login / MFA / self-service must work for any authenticated caller.
    assert rbac.is_authorized(["user"], "POST", "auth/login") is True


def test_reveal_requires_reveal_capability(rbac):
    # An unrecognized role name is default-deny on everything, reveal included.
    assert rbac.is_authorized(["unrecognized-role"], "GET", "credentials/123/reveal") is False
    assert rbac.is_authorized(["admin"], "GET", "credentials/123/reveal") is True


def test_can_reveal_matrix(rbac):
    assert rbac.can_reveal(["admin"]) is True
    assert rbac.can_reveal(["superadmin"]) is True
    assert rbac.can_reveal(["unrecognized-role"]) is False
    assert rbac.can_reveal(["user"]) is False
