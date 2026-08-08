"""Secrets backend abstraction — swap EnvSecretsBackend for VaultSecretsBackend in prod."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsBackend(Protocol):
    def get(self, key: str) -> str:
        """Return the secret value for key, raise RuntimeError if not found."""
        ...


class EnvSecretsBackend:
    """Lab/dev: reads secrets from environment variables.

    Variable names are uppercased and dots replaced with underscores.
    Example: get("pravesh/automation-bridge/api-key") → env PRAVESH_AUTOMATION_BRIDGE_API_KEY
    """

    def get(self, key: str) -> str:
        env_key = key.upper().replace("/", "_").replace("-", "_")
        value = os.environ.get(env_key)
        if not value:
            raise RuntimeError(f"Secret '{key}' not found. Set env var '{env_key}'.")
        return value


class VaultSecretsBackend:
    """Production: reads from HashiCorp Vault via AppRole or AWS IAM auth.

    Requires: pip install pravesh-shared[vault]
    Requires env vars: VAULT_ADDR, VAULT_ROLE_ID, VAULT_SECRET_ID (AppRole)
                    or VAULT_ADDR + AWS IAM role (prod/EKS)
    """

    def __init__(self, mount_point: str = "secret") -> None:
        try:
            import hvac  # noqa: PLC0415
        except ImportError as e:
            raise ImportError("Install vault extra: pip install pravesh-shared[vault]") from e

        self._client = hvac.Client(url=os.environ["VAULT_ADDR"])
        self._mount = mount_point
        self._cache: dict[str, str] = {}
        self._authenticate()

    def _authenticate(self) -> None:
        role_id = os.environ.get("VAULT_ROLE_ID")
        secret_id = os.environ.get("VAULT_SECRET_ID")

        if role_id and secret_id:
            self._client.auth.approle.login(role_id=role_id, secret_id=secret_id)
        elif os.environ.get("AWS_ROLE_ARN") or os.environ.get("ECS_CONTAINER_METADATA_URI"):
            self._client.auth.aws.iam_login(
                role=os.environ["VAULT_AWS_ROLE"],
                use_token=True,
            )
        else:
            raise RuntimeError(
                "Vault auth: set VAULT_ROLE_ID+VAULT_SECRET_ID (AppRole) or configure AWS IAM auth."
            )

    def get(self, key: str) -> str:
        if key in self._cache:
            return self._cache[key]

        # key format: "pravesh/{service}/{secret-name}"
        # maps to Vault path: secret/data/pravesh/{service}/{secret-name}
        parts = key.strip("/").split("/")
        path = "/".join(parts[:-1])
        field = parts[-1]

        response = self._client.secrets.kv.v2.read_secret_version(
            path=path,
            mount_point=self._mount,
        )
        value: str = response["data"]["data"][field]
        self._cache[key] = value
        return value


@lru_cache(maxsize=1)
def get_secrets_backend() -> SecretsBackend:
    """Return the appropriate backend based on environment."""
    if os.environ.get("VAULT_ADDR"):
        return VaultSecretsBackend()
    return EnvSecretsBackend()
