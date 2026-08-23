"""Redaction of secret values from job output.

Job output is persisted to za_job_runs.output_lines and streamed live to the UI.
Nothing scrubbed it, so anything an executor or a script printed was kept
verbatim — a playbook run at -vvv, a script echoing its environment, or an
executor building a command that contains a password all put plaintext secrets
into a retained table and onto an operator's screen. Ansible has `no_log` for
exactly this; there was no equivalent here.

Scope is a ContextVar rather than a module-level set. A process-wide registry
would mask run A's secret out of run B's output — safer in one sense, but it
leaks the fact that a given string is a secret somewhere else, and it grows for
the life of the process. A contextvar is created per run by runner.py, and the
executor inherits it because asyncio copies the context when it creates a task
(the set object is shared, so additions made inside the executor are visible to
the masker outside it).

What this deliberately does NOT do: transform. It masks exact occurrences of
values it was told about. A secret that a script base64s, hashes, or splits
across lines will not be caught, and pretending otherwise would be worse than
the honest limit — masking is a safety net under `no_log` discipline, not a
substitute for it.
"""

from __future__ import annotations

from contextvars import ContextVar

REDACTED = "***REDACTED***"

# Below this length a "secret" is too generic to mask without destroying output:
# masking every occurrence of a 3-character password would redact ordinary words.
MIN_MASKABLE_LEN = 6

_scope: ContextVar[set[str] | None] = ContextVar("job_secret_scope", default=None)


def new_scope() -> set[str]:
    """Start a fresh secret scope for one run and return it."""
    values: set[str] = set()
    _scope.set(values)
    return values


def register(*values: object) -> None:
    """Record secret values that must never appear in output.

    Called wherever a secret is obtained, not where it is used — a value that is
    fetched and then never printed costs nothing to register, whereas one that is
    printed by a path nobody anticipated is exactly the case this exists for.
    """
    scope = _scope.get()
    if scope is None:
        return
    for value in values:
        if isinstance(value, str) and len(value) >= MIN_MASKABLE_LEN:
            scope.add(value)


def register_secret_dict(secret: object) -> None:
    """Register every string in a credential's secret payload.

    Takes the whole dict rather than named keys, so a secret_type added later
    (a token, a certificate) is covered without anyone remembering to come back
    here — the failure mode of a key list is silent.
    """
    if isinstance(secret, dict):
        for value in secret.values():
            register(value)


def mask(text: str) -> str:
    """Replace known secret values in `text`.

    Longest first: if one secret contains another (a passphrase inside a key
    blob), masking the short one first would leave fragments of the long one
    behind, spliced around a REDACTED marker.
    """
    scope = _scope.get()
    if not scope or not text:
        return text
    out = text
    for value in sorted(scope, key=len, reverse=True):
        if value in out:
            out = out.replace(value, REDACTED)
    return out
