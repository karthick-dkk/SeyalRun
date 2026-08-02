"""Pluggable extensibility interfaces.

Every extensibility axis in SeyalRun is a small ABC implemented by a
``app/plugins/<axis>/<name>.py`` module and auto-discovered at startup via
:func:`pluginbase.discover_plugins` — no dynamic ``pip install``, no
hardcoded if/elif branching on plugin name.

Phase 1 ships concrete plugins for ``IdentityProvider`` (identity-service)
and ``CredentialKind`` (inventory-service). ``CommandFilterMatcher``,
``ActionExecutor`` and ``TriggerSource`` are defined now so Phase 1's data
model (command filters, job templates, trigger bindings) slots into them
without rework when terminal-service / automation-service /
zabbix-integration-service land in Phase 2/3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class IdentityProvider(ABC):
    """Authenticates a user and optionally syncs identity from an external source."""

    name: str

    @abstractmethod
    async def authenticate(self, **credentials: Any) -> dict | None:
        """Return a user dict (id, username, role, ...) on success, else None."""

    async def sync_users(self) -> list[dict]:
        """Optional: return a list of user dicts to upsert. Default: no-op."""
        return []


class CredentialKind(ABC):
    """Encodes/decodes/validates one kind of stored credential (password, ssh_key, vault_path, ...)."""

    name: str

    @abstractmethod
    def validate(self, secret: dict) -> None:
        """Raise ValueError if ``secret`` is malformed for this kind."""

    @abstractmethod
    def encode(self, secret: dict) -> str:
        """Serialize ``secret`` to a string suitable for encryption."""

    @abstractmethod
    def decode(self, encoded: str) -> dict:
        """Inverse of :meth:`encode`."""


class KeyProvider(ABC):
    """Wraps/unwraps a per-credential Data Encryption Key (DEK) with a Key
    Encryption Key (KEK) the provider itself owns — PCI DSS Phase C key
    hierarchy. The default ``EnvKeyProvider`` derives its KEK from
    ZA_VAULT_PASSWORD/ZA_VAULT_SALT (same as today), just one layer removed
    from the actual credential ciphertext; a future HSM/cloud-KMS provider is
    a drop-in registered the same way (``app/plugins/kms/<name>.py``),
    without touching callers.
    """

    name: str

    @abstractmethod
    def wrap_dek(self, dek: bytes) -> str:
        """Return an opaque, storable string wrapping ``dek``."""

    @abstractmethod
    def unwrap_dek(self, wrapped: str) -> bytes:
        """Inverse of :meth:`wrap_dek`."""


class CommandFilterMatcher(ABC):
    """Matches a terminal command string against a command-group pattern set.

    Used by ``za_command_filters.match_type``; concrete implementations
    (e.g. ``regex_filter``) are enforced by terminal-service in Phase 2.
    """

    name: str

    @abstractmethod
    def matches(self, command: str, patterns: list[str]) -> bool:
        ...


@dataclass
class RunRequest:
    action_type: str
    target_host_ids: list[str]
    credential_id: str | None
    params: dict = field(default_factory=dict)
    triggered_by: str | None = None  # user id, "zabbix:<event_id>", or "schedule:<id>"


@dataclass
class RunResult:
    ok: bool
    output: str
    exit_code: int | None = None
    details: dict = field(default_factory=dict)


class ActionExecutor(ABC):
    """Runs one ``action_type`` (ansible_playbook, bash_script, account_push, rotate_secret, ...).

    Concrete implementations land with automation-service in Phase 3.
    """

    name: str

    @abstractmethod
    def validate(self, params: dict) -> None:
        """Raise ValueError if ``params`` are invalid for this executor."""

    @abstractmethod
    async def run(self, request: RunRequest) -> RunResult:
        ...


class TriggerSource(ABC):
    """Parses an inbound automation trigger (Zabbix webhook, manual UI button, ...) into a RunRequest.

    Concrete implementations (``zabbix_action_webhook``, ``manual_ui_trigger``)
    land with zabbix-integration-service in Phase 3.
    """

    name: str

    @abstractmethod
    def parse(self, payload: dict) -> RunRequest:
        ...
