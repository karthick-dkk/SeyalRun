"""Credential encryption — AES-256-GCM via libs/dbcore, key derived once at import time.

PCI DSS Phase C: encrypt()/decrypt() (single KEK, no envelope) stay exactly as
they were — still used by log_backend.py for ES/S3 credentials, out of this
phase's scope. encrypt_envelope()/decrypt_envelope() are the new key-hierarchy
path, used only by credentials.py's ZACredential.secret_ciphertext (the actual
PAM vault): a random per-row DEK encrypts the secret, and the active
KeyProvider plugin (env-derived KEK by default) wraps that DEK — so a future
HSM/cloud-KMS provider is a drop-in (app/plugins/kms/<name>.py) that only ever
touches small wrapped-DEK blobs, never re-encrypts every credential in the DB.
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag

from libs.dbcore import decrypt_secret, derive_key, encrypt_secret
from libs.pluginbase import KeyProvider, discover_plugins

from .config import get_settings


class VaultDecryptError(Exception):
    """Raised when a stored ciphertext can't be decrypted with the current key
    (typically ZA_VAULT_PASSWORD/ZA_VAULT_SALT were rotated without re-encrypting)."""


@lru_cache
def _key() -> bytes:
    settings = get_settings()
    return derive_key(settings.za_vault_password, settings.za_vault_salt)


def encrypt(plaintext: str) -> str:
    return encrypt_secret(plaintext, _key())


def decrypt(ciphertext: str) -> str:
    try:
        return decrypt_secret(ciphertext, _key())
    except (InvalidTag, ValueError) as exc:
        # Named, actionable failure instead of a bare 500 / stack trace.
        raise VaultDecryptError(
            "credential could not be decrypted — vault key mismatch. If "
            "ZA_VAULT_PASSWORD/ZA_VAULT_SALT were changed, re-encrypt existing "
            "rows with ops/rotate-vault-key.sh (using the OLD key)."
        ) from exc


_DEK_LEN = 32  # AES-256


@lru_cache
def _key_providers() -> dict[str, KeyProvider]:
    return discover_plugins("app.plugins.kms", KeyProvider)


def _key_provider() -> KeyProvider:
    providers = _key_providers()
    name = get_settings().kms_provider
    return providers.get(name) or providers["env"]


def encrypt_envelope(plaintext: str) -> tuple[str, str]:
    """Returns (ciphertext, wrapped_dek) — a fresh random DEK encrypts plaintext,
    the active KeyProvider wraps the DEK. Both must be stored together."""
    dek = os.urandom(_DEK_LEN)
    ciphertext = encrypt_secret(plaintext, dek)
    wrapped_dek = _key_provider().wrap_dek(dek)
    return ciphertext, wrapped_dek


def decrypt_envelope(ciphertext: str, wrapped_dek: str) -> str:
    try:
        dek = _key_provider().unwrap_dek(wrapped_dek)
        return decrypt_secret(ciphertext, dek)
    except (InvalidTag, ValueError) as exc:
        raise VaultDecryptError(
            "credential could not be decrypted — key-provider mismatch (wrapped "
            "DEK doesn't unwrap under the currently configured KMS_PROVIDER/vault key)."
        ) from exc
