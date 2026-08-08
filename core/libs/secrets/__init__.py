"""Fail-fast validation of required secrets at startup.

Every service calls :func:`require_secrets` from its ``get_settings()`` so a
missing or empty secret aborts boot with a clear message naming the env var,
instead of silently running with an empty signing key (forgeable JWTs),
empty service-token secret (forgeable service tokens), unset vault key
(broken credential crypto), or unset webhook HMAC secret (disabled webhook
auth).

This replaces the previous behaviour where every secret defaulted to "" and
services started anyway — contradicting the README's "services fail fast if a
required var is missing" claim. Fail-fast is hard: there is no warn-mode.
"""

from __future__ import annotations


class MissingSecretError(RuntimeError):
    """Raised at startup when one or more required secrets are unset/empty."""


def require_secrets(settings: object, *field_names: str) -> None:
    """Abort startup if any named settings field is unset or blank.

    ``field_names`` are pydantic settings attribute names (e.g. ``jwt_secret``);
    the corresponding env var is the uppercase form (``JWT_SECRET``), which is
    what the error message reports so an operator knows exactly what to set.
    """
    missing: list[str] = []
    for name in field_names:
        value = getattr(settings, name, None)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            missing.append(name.upper())
    if missing:
        raise MissingSecretError(
            "Refusing to start: required secret(s) unset or empty: "
            + ", ".join(sorted(missing))
            + ". Set them in .env (e.g. generate with `openssl rand -hex 32`, "
            "or `openssl rand -hex 16` for *_SALT)."
        )
