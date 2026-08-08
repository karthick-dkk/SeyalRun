"""Tamper-evident audit hash chain (T9).

Each audit row stores ``seq`` (strict monotonic order), ``prev_hash`` (the
previous row's ``entry_hash``), and ``entry_hash`` = H(seq, prev_hash, payload).
Any edit or deletion of a historical row breaks every subsequent hash, so a
compromised admin cannot quietly rewrite the audit trail — the break is
detectable by :func:`verify_chain`.

The hashing here is pure and deterministic (no DB, no clock), so it is unit
testable in isolation. The serialization (advisory lock + seq assignment) lives
in the service that owns the audit table.
"""

from __future__ import annotations

import hashlib
import json

GENESIS = "0" * 64


def canonical(payload: dict) -> str:
    """Stable serialization: sorted keys, no whitespace, str() for odd types."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_entry_hash(seq: int, prev_hash: str, payload: dict) -> str:
    """Deterministic hash binding a row to its position and its predecessor."""
    material = f"{seq}\n{prev_hash}\n{canonical(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def verify_chain(rows: list[dict]) -> dict:
    """Verify an ordered list of audit rows.

    Each row must have: ``seq``, ``prev_hash``, ``entry_hash``, and ``payload``
    (the dict of hashed fields). ``rows`` must already be ordered by ``seq`` asc.
    Returns ``{"ok": bool, "checked": int, "broken_seq": int|None, "reason": str}``.
    """
    prev = GENESIS
    for row in rows:
        seq = row["seq"]
        if row.get("prev_hash") != prev:
            return {"ok": False, "checked": seq, "broken_seq": seq,
                    "reason": "prev_hash does not match preceding entry (row inserted or deleted)"}
        expected = compute_entry_hash(seq, prev, row["payload"])
        if row.get("entry_hash") != expected:
            return {"ok": False, "checked": seq, "broken_seq": seq,
                    "reason": "entry_hash mismatch (row contents were altered)"}
        prev = row["entry_hash"]
    return {"ok": True, "checked": len(rows), "broken_seq": None, "reason": "chain intact"}
