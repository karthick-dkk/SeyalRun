"""AES-256-GCM credential encryption with a scrypt-derived master key.

The Data Encryption Key is derived from ``ZA_VAULT_PASSWORD`` (>=32 chars)
and ``ZA_VAULT_SALT`` (hex, generate once with ``openssl rand -hex 16``) —
both `.env`-only, never stored in the database. Each encrypted value carries
its own random 12-byte nonce and a 16-byte GCM authentication tag, so
ciphertexts are tamper-evident.

Wire format (base64 of): nonce(12) || ciphertext || tag(16)
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_NONCE_LEN = 12


def derive_key(vault_password: str, vault_salt_hex: str) -> bytes:
    """Derive a 256-bit AES key from the vault password + salt via scrypt."""
    if not vault_password or not vault_salt_hex:
        raise ValueError("ZA_VAULT_PASSWORD and ZA_VAULT_SALT must both be set")
    salt = bytes.fromhex(vault_salt_hex)
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    return kdf.derive(vault_password.encode("utf-8"))


def encrypt_secret(plaintext: str, key: bytes) -> str:
    """Encrypt ``plaintext`` with AES-256-GCM, returning a base64 string."""
    aesgcm = AESGCM(key)
    nonce = os.urandom(_NONCE_LEN)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_secret(ciphertext_b64: str, key: bytes) -> str:
    """Decrypt a base64 string produced by :func:`encrypt_secret`."""
    raw = base64.b64decode(ciphertext_b64)
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
