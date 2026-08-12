"""Service-token invariants: a token is only accepted for its exact audience,
and only while unexpired. This is the spine of the internal trust boundary."""

from __future__ import annotations

import time

import pytest

from libs.servicetoken import ServiceTokenError, mint, verify

SECRET = "test-secret-do-not-use-in-prod-32bytes-minimum!"


def test_mint_then_verify_roundtrip():
    tok = mint("api-gateway", "identity-service", SECRET)
    claims = verify(tok, "identity-service", SECRET)
    assert claims["iss"] == "api-gateway"
    assert claims["aud"] == "identity-service"


def test_wrong_audience_rejected():
    tok = mint("api-gateway", "identity-service", SECRET)
    with pytest.raises(ServiceTokenError):
        verify(tok, "inventory-service", SECRET)  # token minted for a different service


def test_wrong_secret_rejected():
    tok = mint("api-gateway", "identity-service", SECRET)
    with pytest.raises(ServiceTokenError):
        verify(tok, "identity-service", "attacker-secret")


def test_expired_token_rejected():
    tok = mint("api-gateway", "identity-service", SECRET, ttl_seconds=-1)
    with pytest.raises(ServiceTokenError):
        verify(tok, "identity-service", SECRET)


def test_empty_secret_does_not_silently_verify_foreign_token():
    # A token signed with a real secret must not verify under an empty one.
    tok = mint("api-gateway", "identity-service", SECRET)
    with pytest.raises(ServiceTokenError):
        verify(tok, "identity-service", "")
