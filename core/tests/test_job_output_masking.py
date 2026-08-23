"""Secrets must not survive into job output.

za_job_runs.output_lines is persisted and re-read, and the same lines stream live
to the UI. Nothing scrubbed them, so a playbook at -vvv, a script echoing its
environment, or an executor building a command containing a password all wrote
plaintext secrets into a retained table. Ansible has `no_log`; there was no
equivalent here.

The masker is EXECUTED here, not inspected. A redaction routine that is merely
present is the worst kind of security control: it makes the log look reviewed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUTO = ROOT / "services/automation-service/app"
MASKING = AUTO / "_masking.py"
RUNNER = AUTO / "runner.py"


def _load():
    spec = importlib.util.spec_from_file_location("_masking_under_test", MASKING)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def test_module_loads():
    assert M.REDACTED and M.MIN_MASKABLE_LEN >= 4


# ── behaviour ────────────────────────────────────────────────────────────────

def test_masks_a_registered_secret():
    M.new_scope()
    M.register_secret_dict({"password": "hunter2-is-long-enough"})
    assert "hunter2" not in M.mask("login failed for hunter2-is-long-enough")
    assert M.REDACTED in M.mask("x hunter2-is-long-enough y")


def test_registers_every_value_in_a_secret_payload():
    """Taking the whole dict means a secret_type added later is covered without
    anyone remembering to update a key list — the failure mode of a key list is
    silence."""
    M.new_scope()
    M.register_secret_dict({
        "password": "pw-value-long", "private_key": "key-value-long",
        "passphrase": "phrase-value-long", "future_token": "token-value-long",
    })
    out = M.mask("pw-value-long key-value-long phrase-value-long token-value-long")
    assert "value-long" not in out


def test_short_values_are_not_masked():
    """Masking every occurrence of a 3-character password would redact ordinary
    words and make the log unreadable."""
    M.new_scope()
    M.register("abc")
    assert M.mask("abc appears in abcdef") == "abc appears in abcdef"


def test_longest_secret_is_masked_first():
    """If one secret contains another — a passphrase inside a key blob — masking
    the short one first leaves fragments of the long one spliced around a
    marker."""
    M.new_scope()
    M.register("inner-secret", "prefix-inner-secret-suffix")
    out = M.mask("value=prefix-inner-secret-suffix")
    assert out == f"value={M.REDACTED}", out


def test_scopes_do_not_leak_between_runs():
    """A process-wide registry would mask run A's secret out of run B's output —
    which leaks that the string is a secret somewhere, and grows forever."""
    M.new_scope()
    M.register("run-a-secret-value")
    M.new_scope()
    assert M.mask("run-a-secret-value") == "run-a-secret-value"


def test_scope_survives_into_a_child_task():
    """Executors run inside a task the runner creates; asyncio copies the context
    at task creation, so the set object is shared and registrations made inside
    the executor are visible to the masker outside it. If this stopped holding,
    every executor-registered secret would silently stop being masked."""
    async def scenario():
        M.new_scope()

        async def executor():
            M.register_secret_dict({"password": "fetched-inside-executor"})

        await asyncio.wait_for(executor(), timeout=5)
        return M.mask("saw fetched-inside-executor here")

    assert M.REDACTED in asyncio.run(scenario())


def test_masking_is_a_noop_without_a_scope():
    """Code paths outside a run must not crash on an unset contextvar."""
    import contextvars
    ctx = contextvars.copy_context()
    assert ctx.run(M.mask, "anything") == "anything"


# ── wiring ───────────────────────────────────────────────────────────────────

def test_masking_happens_at_the_single_output_choke_point():
    """Both the live stream and the persisted copy go through publish_line.
    Masking one and not the other leaves the secret in the one that is kept."""
    src = RUNNER.read_text()
    fn = src[src.index("async def publish_line"):]
    fn = fn[: fn.index("\n    async with SessionLocal")]
    assert "_masking.mask(line)" in fn
    assert fn.index("_masking.mask(line)") < fn.index("redis.publish"), (
        "the line must be masked before it is published, not after"
    )


def test_scope_is_opened_before_the_first_attempt():
    """A secret registered during attempt 1 must still be masked in attempt 2."""
    src = RUNNER.read_text()
    assert "_masking.new_scope()" in src
    assert src.index("_masking.new_scope()") < src.index("total_attempts = max("), (
        "the scope must exist before any attempt runs"
    )


@pytest.mark.parametrize("path,marker", [
    ("_account_ops.py", "register_secret_dict"),
    ("plugins/executors/bash_script.py", "register_secret_dict"),
    ("plugins/executors/rotate_secret.py", "_masking.register(new_password)"),
])
def test_secrets_are_registered_where_they_are_obtained(path: str, marker: str):
    """Registering at the point of use would miss every path that prints a secret
    without meaning to — which is the case masking exists for."""
    assert marker in (AUTO / path).read_text(), f"{path} does not register its secrets"


def test_every_credential_fetch_registers_its_secret():
    """A fetch site that forgets is a silent hole; this counts them."""
    src = (AUTO / "plugins/executors/bash_script.py").read_text()
    fetches = len(re.findall(r"/internal/credentials/\{[^}]+\}/secret", src))
    registers = src.count("register_secret_dict")
    assert registers >= fetches, (
        f"{fetches} credential fetch(es) but only {registers} registration(s)"
    )
