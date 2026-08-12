from __future__ import annotations

import json

from libs.pluginbase import CredentialKind


class SSHKeyCredential(CredentialKind):
    name = "ssh_key"

    def validate(self, secret: dict) -> None:
        if not secret.get("private_key"):
            raise ValueError("ssh_key credential requires a non-empty 'private_key'")

    def encode(self, secret: dict) -> str:
        return json.dumps({"private_key": secret["private_key"], "passphrase": secret.get("passphrase", "")})

    def decode(self, encoded: str) -> dict:
        return json.loads(encoded)
