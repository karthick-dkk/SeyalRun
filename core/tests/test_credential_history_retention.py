"""Rotation keeps a bounded number of prior secrets, and never a plaintext one.

Two invariants, both structural, both about the same risk: an old password is
still a live secret for anything that was never re-keyed.

1. The archive is capped. It was unbounded, so a credential rotated weekly
   accumulated an ever-growing set of decryptable former passwords — and every
   one of them is a row ops/rotate_vault_key.py must keep rewrapping forever.

2. Nothing on the history row is plaintext. The archived secret must be
   ciphertext with its own wrapped_dek, because the live row's wrapped_dek is
   overwritten by the very rotation that creates the history row (see the model
   docstring) — an archived ciphertext without its DEK is permanently
   undecryptable, and an archived *plaintext* is a vault bypass.

Source-shape checks: the invariant suite has no database, and the failure here
is structural (a missing cap, a plaintext column) rather than behavioural.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDS_API = ROOT / "services/inventory-service/app/api/credentials.py"
MODELS = ROOT / "services/inventory-service/app/models.py"
ROTATE = ROOT.parent / "ops/rotate_vault_key.py"


def _history_model() -> str:
    src = MODELS.read_text()
    start = src.index("class ZACredentialHistory")
    return src[start : src.index("\nclass ", start + 1)]


def test_history_model_parses():
    """Guard against the slice above silently returning nothing."""
    assert "secret_ciphertext" in _history_model()


def test_history_is_capped_on_rotation():
    src = CREDS_API.read_text()
    assert "SECRET_HISTORY_KEEP" in src, "rotation must bound how many prior secrets it keeps"
    m = re.search(r"SECRET_HISTORY_KEEP\s*=\s*(\d+)", src)
    assert m and 1 <= int(m.group(1)) <= 5, f"unreasonable retention: {m and m.group(1)}"
    # The cap has to be enforced, not merely declared.
    assert "delete(ZACredentialHistory)" in src, "the cap must actually prune old rows"


def test_archived_secret_is_ciphertext_with_its_own_dek():
    model = _history_model()
    assert "secret_ciphertext" in model
    assert "wrapped_dek" in model, (
        "the archived ciphertext needs the DEK that can unwrap it — the live row's "
        "wrapped_dek is overwritten by the same rotation that writes this row"
    )
    # No column that would hold readable secret material.
    for banned in ("plaintext", "secret_plain", "password:", "secret: Mapped[str]"):
        assert banned not in model, f"history row must not carry {banned!r}"


def test_rotation_script_covers_the_history_table():
    """A table holding wrapped secrets that KEK rotation skips becomes
    permanently undecryptable at the next rotation — this is R-6's shape."""
    src = ROTATE.read_text()
    m = re.search(r"TABLES\s*=\s*\(([^)]*)\)", src)
    assert m, "rotate_vault_key.py must declare the tables it rotates"
    assert "za_credential_history" in m.group(1), (
        "credential history holds wrapped secrets and must be rewrapped on KEK rotation"
    )
