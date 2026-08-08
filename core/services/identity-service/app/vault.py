"""TOTP secret encryption — AES-256-GCM via libs/dbcore, shared vault key with inventory-service."""

from __future__ import annotations

from functools import lru_cache

from libs.dbcore import decrypt_secret, derive_key, encrypt_secret

from .config import get_settings


@lru_cache
def _key() -> bytes:
    settings = get_settings()
    return derive_key(settings.za_vault_password, settings.za_vault_salt)


def encrypt(plaintext: str) -> str:
    return encrypt_secret(plaintext, _key())


def decrypt(ciphertext: str) -> str:
    return decrypt_secret(ciphertext, _key())
