"""Self-service access requests — the rules both sides share.

A user who can SEE a host but not log in to it asks for a time-boxed login
(api/access_requests.py). The request is an ordinary za_authorizations row with
request_kind = USER_REQUEST, so approval, expiry, the resolvers and every
consumer treat it exactly like an admin-written rule once it is active.

Two properties are specific to requests and live here so that the requester-side
API and the approver-side API (api/authorizations.py: approve, reject and the
email links) cannot drift apart:

  * decided by someone else, always. The general self-approval block compares
    the approver to requested_by; for a request that is the beneficiary too, but
    this check names the beneficiary explicitly and does not depend on any
    platform-wide approval policy, so relaxing admin self-approval elsewhere can
    never let a user's own request through.
  * the window starts at approval. A request asks for "8 hours", not "until 17:00
    today" — waiting in the queue must not eat into it, and a request approved
    after its original end must still grant what was asked for.

Stdlib + FastAPI's HTTPException only, so the suite imports it directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException, status

USER_REQUEST = "user_request"

# value -> (minutes, label). The only durations a user may ask for.
DURATIONS: dict[str, tuple[int, str]] = {
    "1h": (60, "1 hour"),
    "8h": (8 * 60, "8 hours"),
    "1d": (24 * 60, "1 day"),
    "7d": (7 * 24 * 60, "7 days"),
}

# What an approved request grants: an interactive login with the one account
# asked for. Not sftp/upload/download/reveal — each of those is its own action,
# and a user who needs one asks an admin for a rule that says so.
REQUEST_ACTIONS = ["ssh"]

REASON_MIN, REASON_MAX = 3, 1000
REASON_MAX_LINES = 20


def clean_reason(value: str) -> str:
    """Validate and normalise a request reason. Raises ValueError.

    The reason is free text shown to approvers (console and email), so it is
    held to printable text: line breaks are allowed — a reason can need a few
    lines — but normalised (\\r\\n and \\r become \\n, at most REASON_MAX_LINES
    lines), tabs become spaces, and every other control or format character
    (escape sequences, NUL, bidi overrides, zero-width marks, U+2028/2029,
    \\x85, \\v, \\f ...) is refused rather than silently kept. The email builder
    (app/approvalmail.py) quotes it again regardless — this is the first layer.
    """
    import unicodedata

    v = (value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    for ch in v:
        if ch != "\n" and unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"):
            raise ValueError(f"reason contains a control or invisible character (U+{ord(ch):04X})")
    lines = [line.rstrip() for line in v.strip().split("\n")]
    if len(lines) > REASON_MAX_LINES:
        raise ValueError(f"reason may have at most {REASON_MAX_LINES} lines")
    v = "\n".join(lines).strip()
    if not REASON_MIN <= len(v) <= REASON_MAX:
        raise ValueError(f"reason must be {REASON_MIN}-{REASON_MAX} characters")
    return v


def beneficiaries(authz) -> set[str]:
    out = set(authz.user_ids or [])
    if authz.user_id:
        out.add(authz.user_id)
    if authz.requested_by:
        out.add(authz.requested_by)
    return out


def guard_decision(authz, actor_id: str | None) -> None:
    """Refuse a decision on a user's access request by the requester or anyone the
    request would grant access to. No-op for admin-written rules."""
    if getattr(authz, "request_kind", None) != USER_REQUEST:
        return
    if not actor_id or actor_id in beneficiaries(authz):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="an access request must be approved or rejected by someone other than the requester",
        )


def start_window(authz, now: datetime) -> None:
    """On approval: the requested duration runs from now. No-op for admin rules."""
    if getattr(authz, "request_kind", None) != USER_REQUEST:
        return
    minutes = authz.request_duration_minutes or 0
    if minutes <= 0:
        # A request with no duration must not become an open-ended grant.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="access request has no duration")
    authz.date_start = now
    authz.date_expired = now + timedelta(minutes=minutes)


def display_status(authz, now: datetime) -> str:
    """The status a requester should see: an approved request past its window is
    expired even before the housekeeping sweep flips it."""
    if authz.status == "active" and authz.date_expired and authz.date_expired < now:
        return "expired"
    return authz.status
