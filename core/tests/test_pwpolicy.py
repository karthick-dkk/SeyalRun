"""Forced first-login rotation: the new-password policy must reject the shipped
default, the username, a too-short value, and a no-op change — otherwise the
"must change password" gate rotates the default onto itself. Pure/dependency-free."""

from __future__ import annotations

import pytest


@pytest.fixture
def policy(load_module):
    return load_module("services/identity-service/app/_pwpolicy.py", "za_pwpolicy")


def test_good_password_accepted(policy):
    assert policy.validate_new_password("Admin", "seyalrun", "S3cure-Pass!") is None


def test_too_short_rejected(policy):
    assert "at least 8" in policy.validate_new_password("Admin", "seyalrun", "short")


def test_same_as_current_rejected(policy):
    assert "differ" in policy.validate_new_password("Admin", "seyalrun1", "seyalrun1")


def test_shipped_default_rejected_case_insensitive(policy):
    for candidate in ("seyalrun", "SeyalRun", "SEYALRUN"):
        assert "default" in policy.validate_new_password("Admin", "old-pass-1", candidate)


def test_username_rejected_case_insensitive(policy):
    assert "username" in policy.validate_new_password("Administrator1", "old-pass", "administrator1")


def test_exactly_min_length_accepted(policy):
    assert policy.validate_new_password("Admin", "seyalrun", "a" * policy.MIN_LENGTH) is None
