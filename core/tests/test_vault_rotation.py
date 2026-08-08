"""Vault key rotation invariant: re-encrypting under a new key round-trips, and
the old key can no longer read the rotated ciphertext. Mirrors ops/rotate-vault-key.sh."""

from __future__ import annotations

import pytest


@pytest.fixture
def crypto(load_module):
    return load_module("libs/dbcore/crypto.py", "za_crypto_rot")


def test_rotation_reencrypt_roundtrip(crypto):
    old = crypto.derive_key("old-vault-password-1234567890abc", "00112233445566778899aabbccddeeff")
    new = crypto.derive_key("new-vault-password-zyxwvutsrqpo", "ffeeddccbbaa99887766554433221100")

    ct_old = crypto.encrypt_secret("s3cr3t", old)
    assert crypto.decrypt_secret(ct_old, old) == "s3cr3t"

    # Rotate: decrypt with the OLD key, re-encrypt under the NEW key.
    plain = crypto.decrypt_secret(ct_old, old)
    ct_new = crypto.encrypt_secret(plain, new)
    assert crypto.decrypt_secret(ct_new, new) == "s3cr3t"


def test_old_key_cannot_read_rotated_ciphertext(crypto):
    old = crypto.derive_key("old-vault-password-1234567890abc", "00112233445566778899aabbccddeeff")
    new = crypto.derive_key("new-vault-password-zyxwvutsrqpo", "ffeeddccbbaa99887766554433221100")
    ct_new = crypto.encrypt_secret("s3cr3t", new)
    with pytest.raises(Exception):  # InvalidTag — this is what VaultDecryptError wraps
        crypto.decrypt_secret(ct_new, old)
