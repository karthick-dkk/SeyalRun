"""New-password policy for the forced first-login rotation.

Pure/dependency-free (stdlib only) so tests/ can load it directly by path,
like _lockout and the other security-invariant modules.
"""

from __future__ import annotations

MIN_LENGTH = 8

# The shipped default — a new password may never be (or trivially contain) it.
DEFAULT_SEED_PASSWORD = "seyalrun"


def validate_new_password(username: str, current_password: str, new_password: str) -> str | None:
    """Return a human-readable rejection reason, or None if acceptable."""
    if len(new_password) < MIN_LENGTH:
        return f"new password must be at least {MIN_LENGTH} characters"
    if new_password == current_password:
        return "new password must differ from the current password"
    lowered = new_password.lower()
    if lowered == DEFAULT_SEED_PASSWORD:
        return "new password must not be the shipped default"
    if username and lowered == username.lower():
        return "new password must not be the username"
    return None
