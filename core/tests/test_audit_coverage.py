"""Audit coverage invariants for the paths closed in risk register R-2/R-3/R-4.

These mirror the implementations rather than importing them: the services pull in
FastAPI, SQLAlchemy and httpx, and this suite is deliberately runnable with only
pytest + pyjwt + cryptography + pyyaml (see tests/requirements-test.txt).
"""

from __future__ import annotations

import hashlib
import json

import pytest

# ── Job attribution (R-3) — mirrors automation-service/app/runner.py::_actor_id ──

_NON_USER_TRIGGER_PREFIXES = ("zabbix:", "schedule:")


def actor_id(triggered_by: str | None) -> str | None:
    if not triggered_by or triggered_by.startswith(_NON_USER_TRIGGER_PREFIXES):
        return None
    return triggered_by


@pytest.mark.parametrize(
    "triggered_by",
    ["zabbix:10453", "schedule:7", "", None],
)
def test_machine_triggers_are_not_attributed_to_a_user(triggered_by):
    """A scheduled or Zabbix-driven run must not land in user_id, or the audit
    log would show "schedule:7" as though it were a user account."""
    assert actor_id(triggered_by) is None


def test_user_triggered_run_keeps_its_user_id():
    assert actor_id("6f1c2f8e-0b1a-4a55-9a1e-3c0d5f6a7b88") == "6f1c2f8e-0b1a-4a55-9a1e-3c0d5f6a7b88"


def test_a_user_id_beginning_with_a_word_like_scheduled_is_not_dropped():
    """Prefix matching must be on the marker, not a loose substring."""
    assert actor_id("scheduled-user-42") == "scheduled-user-42"


# ── Recording integrity (R-4) — mirrors recording-service/app/api/internal.py ──


def canonical_frames(frames: list[dict]) -> str:
    return json.dumps(frames, sort_keys=True, separators=(",", ":"), default=str)


def digest(frames: list[dict]) -> str:
    return hashlib.sha256(canonical_frames(frames).encode("utf-8")).hexdigest()


FRAMES = [{"t": 0.5, "d": "ls -la\n"}, {"t": 1.2, "d": "total 0\n"}]


def test_digest_is_stable_across_key_order():
    """The hash is taken at ingest and re-checked after the value has round-tripped
    through the database's JSON type, which does not promise key order."""
    reordered = [{"d": "ls -la\n", "t": 0.5}, {"d": "total 0\n", "t": 1.2}]
    assert digest(FRAMES) == digest(reordered)


def test_altered_frame_content_is_detected():
    tampered = [{"t": 0.5, "d": "ls -la\n"}, {"t": 1.2, "d": "total 999\n"}]
    assert digest(tampered) != digest(FRAMES)


def test_removed_frame_is_detected():
    assert digest(FRAMES[:1]) != digest(FRAMES)


def test_reordered_frames_are_detected():
    """Frame order is the session's chronology — swapping it rewrites history."""
    assert digest(list(reversed(FRAMES))) != digest(FRAMES)


def test_empty_recording_still_has_a_digest():
    assert len(digest([])) == 64


def test_missing_digest_is_reported_not_passed():
    """Recordings predating the column cannot be verified. The endpoint must say
    so rather than returning ok, which would certify content nobody checked."""

    def verify(stored_digest: str | None, frames: list[dict]) -> dict:
        if not stored_digest:
            return {"ok": False, "reason": "no digest recorded"}
        return {"ok": stored_digest == digest(frames)}

    assert verify(None, FRAMES) == {"ok": False, "reason": "no digest recorded"}
    assert verify("", FRAMES)["ok"] is False
    assert verify(digest(FRAMES), FRAMES)["ok"] is True


# ── Session end (R-2) ──


def test_session_end_reason_distinguishes_termination_from_disconnect():
    """Mirrors ws/terminal.py: an admin kill and a user disconnect must not be
    indistinguishable in the audit log."""

    def reason(terminated: bool, status: str) -> str:
        return "terminated" if terminated else status

    assert reason(True, "closed") == "terminated"
    assert reason(False, "closed") == "closed"
    assert reason(False, "error") == "error"
