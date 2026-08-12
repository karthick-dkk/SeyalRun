from __future__ import annotations

import json

from libs.pluginbase import CredentialKind


class PasswordCredential(CredentialKind):
    name = "password"

    def validate(self, secret: dict) -> None:
        if not secret.get("password"):
            raise ValueError("password credential requires a non-empty 'password'")

    def encode(self, secret: dict) -> str:
        return json.dumps({"password": secret["password"]})

    def decode(self, encoded: str) -> dict:
        return json.loads(encoded)
