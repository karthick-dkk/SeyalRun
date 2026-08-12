"""Every field bound into the audit hash must also be persisted on the row.

The chain hashes a payload built by audit_payload() and stores the result in
entry_hash. Verification rebuilds that payload *from the stored row*. So if a
field is hashed but never written to the row, the rebuilt payload is missing a
key the hash was computed over, and verify_chain reports

    "entry_hash mismatch (row contents were altered)"

for a row nobody touched. Permanently, and for every entry carrying that field.

That is exactly what happened: migration 017 added session_id and result, and
audit_payload started including them, but log_action's ZAAuditLog(...) call was
never updated to persist them. 84 of 90 rows on staging were unverifiable, and
nothing noticed because the evidence collected was a count of hashed rows rather
than a verification of them.

This is a source-shape check, deliberately. The invariant suite has no database,
and a behavioural test would need one — but the failure is structural (two lists
of field names drifting apart), so a structural test catches it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

AUDIT = Path(__file__).resolve().parents[1] / "services" / "identity-service" / "app" / "audit.py"


def _hashed_fields() -> set[str]:
    """Keys audit_payload() can put into the hashed payload."""
    source = AUDIT.read_text()
    body = source.split("def audit_payload", 1)[1].split("async def log_action", 1)[0]
    return set(re.findall(r'payload\["(\w+)"\]\s*=', body)) | set(
        re.findall(r'^\s{8}"(\w+)":', body, re.MULTILINE)
    )


def _persisted_fields() -> set[str]:
    """Columns log_action() actually writes on the ZAAuditLog row.

    Scans to the balanced closing paren rather than the first one — the
    constructor contains comments, and a comment with a bracket in it silently
    truncated an earlier version of this parser, making the test fail against
    correct code.
    """
    source = AUDIT.read_text()
    start = source.index("ZAAuditLog(") + len("ZAAuditLog(")
    depth, end = 1, start
    while depth:
        ch = source[end]
        depth += (ch == "(") - (ch == ")")
        end += 1
    ctor = source[start:end - 1]
    # Strip comments so commented-out field names cannot count as persisted.
    ctor = "\n".join(line.split("#", 1)[0] for line in ctor.splitlines())
    return set(re.findall(r"(\w+)\s*=", ctor))


# created_at is hashed truncated to the second but stored at full precision, and
# the chain columns are not part of the payload — both are intentional.
NOT_PERSISTED_VERBATIM = {"created_at"}


def test_audit_payload_has_recognisable_fields():
    """Guard against the parsing above silently returning nothing, which would
    make every check below vacuously pass."""
    fields = _hashed_fields()
    assert {"action", "user_id", "details"} <= fields, f"parsed unexpectedly: {fields}"


@pytest.mark.parametrize("field", sorted(_hashed_fields() - NOT_PERSISTED_VERBATIM))
def test_every_hashed_field_is_persisted(field: str):
    """A field in the hash but not on the row makes the row unverifiable forever."""
    persisted = _persisted_fields()
    assert field in persisted, (
        f"audit_payload() hashes {field!r} but log_action() never persists it. "
        "Verification rebuilds the payload from the stored row, so every entry "
        "carrying this field will fail verify_chain as 'contents were altered'."
    )


def test_session_id_and_result_specifically():
    """Explicit regression for the two fields that actually broke the chain."""
    persisted = _persisted_fields()
    for field in ("session_id", "result"):
        assert field in persisted, f"{field} is hashed but not persisted — the 017 regression"
