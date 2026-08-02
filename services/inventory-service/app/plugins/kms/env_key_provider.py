from __future__ import annotations

import base64

from libs.dbcore import decrypt_secret, derive_key, encrypt_secret
from libs.pluginbase import KeyProvider

from ...config import get_settings


class EnvKeyProvider(KeyProvider):
    """Default KeyProvider — the KEK is the same ZA_VAULT_PASSWORD/ZA_VAULT_SALT-
    derived key the vault used directly before Phase C's envelope encryption.
    Wrapping a DEK is just AES-256-GCM over its base64 text, reusing the exact
    primitive already used for credential secrets (libs/dbcore)."""

    name = "env"

    def __init__(self) -> None:
        settings = get_settings()
        self._kek = derive_key(settings.za_vault_password, settings.za_vault_salt)

    def wrap_dek(self, dek: bytes) -> str:
        return encrypt_secret(base64.b64encode(dek).decode("ascii"), self._kek)

    def unwrap_dek(self, wrapped: str) -> bytes:
        return base64.b64decode(decrypt_secret(wrapped, self._kek))
