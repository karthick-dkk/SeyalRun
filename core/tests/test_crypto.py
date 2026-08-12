"""Credential-vault crypto invariants: AES-256-GCM round-trips, a wrong key
fails loudly (not silently), and any tampering is detected (auth tag)."""

from __future__ import annotations

import base64

import pytest


@pytest.fixture
def crypto(load_module):
    # Load by path to skip libs/dbcore/__init__ (which pulls in SQLAlchemy).
    return load_module("libs/dbcore/crypto.py", "za_crypto")


def _key(crypto, pw="a-very-long-vault-password-32chars!!", salt_hex="00112233445566778899aabbccddeeff"):
    return crypto.derive_key(pw, salt_hex)


def test_encrypt_decrypt_roundtrip(crypto):
    key = _key(crypto)
    ct = crypto.encrypt_secret("hunter2", key)
    assert crypto.decrypt_secret(ct, key) == "hunter2"


def test_nonce_is_random_per_encryption(crypto):
    key = _key(crypto)
    assert crypto.encrypt_secret("same", key) != crypto.encrypt_secret("same", key)


def test_wrong_key_fails_loudly(crypto):
    ct = crypto.encrypt_secret("secret", _key(crypto))
    other = _key(crypto, salt_hex="ffffffffffffffffffffffffffffffff")
    with pytest.raises(Exception):  # cryptography raises InvalidTag
        crypto.decrypt_secret(ct, other)


def test_tampered_ciphertext_detected(crypto):
    key = _key(crypto)
    ct = crypto.encrypt_secret("secret", key)
    raw = bytearray(base64.b64decode(ct))
    raw[-1] ^= 0x01  # flip a bit in the auth tag
    tampered = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(Exception):
        crypto.decrypt_secret(tampered, key)


def test_derive_key_requires_material(crypto):
    with pytest.raises(ValueError):
        crypto.derive_key("", "00112233445566778899aabbccddeeff")
