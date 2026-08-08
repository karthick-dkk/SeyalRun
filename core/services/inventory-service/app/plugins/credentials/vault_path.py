from __future__ import annotations

import json

from libs.pluginbase import CredentialKind


class VaultPathCredential(CredentialKind):
    """A reference to a secret stored in an external vault (e.g. HashiCorp Vault).

    Only the path/reference is stored in `secret_ciphertext` (still
    encrypted at rest) — the actual secret material lives in the external
    system and is fetched at use-time (Phase 2/3).
    """

    name = "vault_path"

    def validate(self, secret: dict) -> None:
        if not secret.get("path"):
            raise ValueError("vault_path credential requires a non-empty 'path'")

    def encode(self, secret: dict) -> str:
        return json.dumps({"path": secret["path"]})

    def decode(self, encoded: str) -> dict:
        return json.loads(encoded)
