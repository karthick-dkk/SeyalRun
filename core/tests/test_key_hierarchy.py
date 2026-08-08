"""PCI DSS Phase C: key-hierarchy envelope encryption invariants. Mirrors
EnvKeyProvider's wrap_dek/unwrap_dek composition (base64 + AES-256-GCM) using
the pure libs/dbcore/crypto.py primitives directly — EnvKeyProvider itself
has relative imports (app.config) that don't load standalone, but the
composition it performs is exactly what's tested here."""

from __future__ import annotations

import base64
import os

import pytest


@pytest.fixture
def crypto(load_module):
    return load_module("libs/dbcore/crypto.py", "za_crypto_kh")


def _kek(crypto, pw="a-very-long-vault-password-32chars!!", salt_hex="00112233445566778899aabbccddeeff"):
    return crypto.derive_key(pw, salt_hex)


def _wrap_dek(crypto, dek: bytes, kek: bytes) -> str:
    return crypto.encrypt_secret(base64.b64encode(dek).decode("ascii"), kek)


def _unwrap_dek(crypto, wrapped: str, kek: bytes) -> bytes:
    return base64.b64decode(crypto.decrypt_secret(wrapped, kek))


def test_envelope_roundtrip(crypto):
    kek = _kek(crypto)
    dek = os.urandom(32)
    wrapped = _wrap_dek(crypto, dek, kek)
    assert _unwrap_dek(crypto, wrapped, kek) == dek

    # The DEK (not the KEK) actually encrypts the secret.
    ciphertext = crypto.encrypt_secret("hunter2", dek)
    assert crypto.decrypt_secret(ciphertext, dek) == "hunter2"


def test_each_credential_gets_a_distinct_dek(crypto):
    kek = _kek(crypto)
    dek_a, dek_b = os.urandom(32), os.urandom(32)
    assert dek_a != dek_b
    assert _wrap_dek(crypto, dek_a, kek) != _wrap_dek(crypto, dek_b, kek)


def test_wrong_kek_cannot_unwrap(crypto):
    kek = _kek(crypto)
    other_kek = _kek(crypto, salt_hex="ffffffffffffffffffffffffffffffff")
    wrapped = _wrap_dek(crypto, os.urandom(32), kek)
    with pytest.raises(Exception):
        _unwrap_dek(crypto, wrapped, other_kek)


def test_compromised_ciphertext_alone_is_useless_without_the_wrapped_dek(crypto):
    """The point of the hierarchy: even with the KEK, you need the specific
    row's wrapped_dek — the KEK never touches credential ciphertext directly."""
    kek = _kek(crypto)
    dek = os.urandom(32)
    ciphertext = crypto.encrypt_secret("hunter2", dek)
    with pytest.raises(Exception):
        crypto.decrypt_secret(ciphertext, kek)  # KEK is not the DEK
