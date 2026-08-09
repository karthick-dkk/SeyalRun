"""KEK rotation must work for envelope-encrypted credentials.

Mirrors ops/rotate_vault_key.py. Envelope rows are the actual PAM vault: the
secret is encrypted under a random per-row DEK, and the KEK only wraps that DEK
(inventory-service/app/vault.py, plugins/kms/env_key_provider.py).

The point of the key hierarchy is that rotating the KEK rewrites only small
wrapped-DEK blobs and never re-encrypts every credential — so a rotation that
touches secret_ciphertext is not just wasteful, it is wrong.
"""

from __future__ import annotations

import base64
import os
import pathlib

import pytest
from cryptography.exceptions import InvalidTag


@pytest.fixture
def crypto(load_module):
    return load_module("libs/dbcore/crypto.py", "za_crypto_kek_rot")


def _wrap(crypto, dek: bytes, kek: bytes) -> str:
    """EnvKeyProvider.wrap_dek."""
    return crypto.encrypt_secret(base64.b64encode(dek).decode("ascii"), kek)


def _unwrap(crypto, wrapped: str, kek: bytes) -> bytes:
    """EnvKeyProvider.unwrap_dek."""
    return base64.b64decode(crypto.decrypt_secret(wrapped, kek))


def _keys(crypto):
    return (
        crypto.derive_key("old-vault-password-1234567890abc", "00" * 16),
        crypto.derive_key("new-vault-password-zyxwvutsrqpo", "ff" * 16),
    )


def test_rewrapping_the_dek_preserves_the_secret(crypto):
    old_kek, new_kek = _keys(crypto)
    dek = os.urandom(32)
    ciphertext = crypto.encrypt_secret("s3cr3t", dek)
    wrapped = _wrap(crypto, dek, old_kek)

    # Rotation: re-encrypt the wrapped-DEK blob only, leaving ciphertext alone.
    dek_b64 = crypto.decrypt_secret(wrapped, old_kek)
    rewrapped = crypto.encrypt_secret(dek_b64, new_kek)

    assert _unwrap(crypto, rewrapped, new_kek) == dek
    assert crypto.decrypt_secret(ciphertext, _unwrap(crypto, rewrapped, new_kek)) == "s3cr3t"


def test_old_kek_cannot_unwrap_after_rotation(crypto):
    old_kek, new_kek = _keys(crypto)
    dek = os.urandom(32)
    wrapped = _wrap(crypto, dek, old_kek)
    rewrapped = crypto.encrypt_secret(crypto.decrypt_secret(wrapped, old_kek), new_kek)

    with pytest.raises(InvalidTag):
        _unwrap(crypto, rewrapped, old_kek)


def test_credential_ciphertext_must_not_be_touched(crypto):
    """Regression for the original script, which decrypted secret_ciphertext with
    the KEK. For an envelope row that is the wrong key, and AES-GCM authenticates,
    so it raises rather than silently corrupting — which is why rotation died on
    the first envelope row instead of completing."""
    old_kek, _ = _keys(crypto)
    dek = os.urandom(32)
    ciphertext = crypto.encrypt_secret("s3cr3t", dek)

    with pytest.raises(InvalidTag):
        crypto.decrypt_secret(ciphertext, old_kek)


def test_legacy_single_kek_rows_still_rotate(crypto):
    """Rows predating envelope encryption keep the old path: the secret itself is
    under the KEK, so it is re-encrypted."""
    old_kek, new_kek = _keys(crypto)
    ciphertext = crypto.encrypt_secret("legacy", old_kek)

    rotated = crypto.encrypt_secret(crypto.decrypt_secret(ciphertext, old_kek), new_kek)

    assert crypto.decrypt_secret(rotated, new_kek) == "legacy"
    with pytest.raises(InvalidTag):
        crypto.decrypt_secret(rotated, old_kek)


def test_rotation_is_classified_by_wrapped_dek_presence(crypto):
    """How the script decides which path a row takes."""
    rows = [
        {"id": "a", "wrapped_dek": _wrap(crypto, os.urandom(32), _keys(crypto)[0])},
        {"id": "b", "wrapped_dek": None},
        {"id": "c", "wrapped_dek": ""},
    ]
    envelope = [r for r in rows if r["wrapped_dek"]]
    legacy = [r for r in rows if not r["wrapped_dek"]]

    assert [r["id"] for r in envelope] == ["a"]
    assert [r["id"] for r in legacy] == ["b", "c"]


def test_history_table_must_rotate_too(crypto):
    """Regression for the archive-loss bug: za_credential_history carries its own
    wrapped_dek, so rotating only za_credentials leaves every archived version
    permanently unreadable once the old KEK is discarded — silently, while the
    script reports success."""
    old_kek, new_kek = _keys(crypto)

    live_dek, archived_dek = os.urandom(32), os.urandom(32)
    live_wrapped = _wrap(crypto, live_dek, old_kek)
    archived_wrapped = _wrap(crypto, archived_dek, old_kek)
    archived_ct = crypto.encrypt_secret("previous-password", archived_dek)

    # Rotate ONLY the live row, as the buggy version did.
    live_rotated = crypto.encrypt_secret(crypto.decrypt_secret(live_wrapped, old_kek), new_kek)
    assert _unwrap(crypto, live_rotated, new_kek) == live_dek

    # The archive is now orphaned: the new KEK cannot unwrap it...
    with pytest.raises(InvalidTag):
        _unwrap(crypto, archived_wrapped, new_kek)
    # ...and once the old KEK is discarded, nothing can. The ciphertext survives
    # but is undecryptable forever.
    assert archived_ct

    # Rotating BOTH tables keeps the archive readable.
    archived_rotated = crypto.encrypt_secret(crypto.decrypt_secret(archived_wrapped, old_kek), new_kek)
    recovered_dek = _unwrap(crypto, archived_rotated, new_kek)
    assert recovered_dek == archived_dek
    assert crypto.decrypt_secret(archived_ct, recovered_dek) == "previous-password"


def test_rotation_covers_every_table_carrying_wrapped_dek():
    """The script must enumerate both tables; missing one is silent and permanent."""
    script = (pathlib.Path(__file__).resolve().parents[2] / "ops" / "rotate_vault_key.py").read_text()
    for table in ("za_credentials", "za_credential_history"):
        assert table in script, f"rotate_vault_key.py never touches {table}"
