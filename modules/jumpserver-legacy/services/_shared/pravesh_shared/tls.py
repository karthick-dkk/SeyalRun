"""TLS trust resolution for outbound HTTP clients."""

from __future__ import annotations

import os


def verify_for(env_var: str) -> str | bool:
    """Return a value for httpx's ``verify`` argument.

    Trust may only be *widened* — pointing at a private CA bundle keeps
    self-signed lab certificates working — never disabled. A bundle path that
    does not exist raises rather than silently falling back to system CAs,
    so a typo cannot quietly downgrade which certificates are accepted.
    """
    bundle = os.getenv(env_var, "").strip()
    if not bundle:
        return True
    if not os.path.isfile(bundle):
        raise RuntimeError(
            f"{env_var} points at '{bundle}', which is not a readable file. "
            "Provide a valid PEM CA bundle or unset it to use system CAs."
        )
    return bundle
