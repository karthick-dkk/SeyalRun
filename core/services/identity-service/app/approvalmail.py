"""The approver email for a pending access rule — built as plain data, no I/O.

Requester-supplied text (an access request's reason) reaches an admin's inbox
next to one-click approve/reject links, so it is a phishing surface: a reason
containing "\\nApprove: https://evil…" must never read as a system line. Two
rules hold here:

  * every system line (names, links, expiry) is written first and is
    single-line — one_line() collapses line breaks and control/format characters
    even in admin-controlled values (host or user names synced from Zabbix);
  * user text comes LAST, inside a labelled block where every line starts with
    "> ", so nothing the requester types can start a line outside it.

Stdlib only, so the suite tests it directly.
"""

from __future__ import annotations

import unicodedata

USER_REQUEST = "user_request"
MAX_QUOTED_LINES = 20


def _clean_char(ch: str) -> str:
    # Cc (C0/C1 controls incl. \t, \x1b), Cf (bidi overrides, zero-width), Zl/Zp
    # (U+2028/2029 — line breaks to many clients) all become a plain space.
    return " " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch


def one_line(text: object, limit: int = 300) -> str:
    """A value safe to place on a single system line."""
    s = "".join(_clean_char(c) for c in str(text or ""))
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def quote_untrusted(text: str | None, max_lines: int = MAX_QUOTED_LINES) -> list[str]:
    """User text as '> '-prefixed lines. splitlines() breaks on every line
    boundary Python knows (\\n, \\r, \\v, \\f, \\x1c-\\x1e, \\x85, U+2028/2029), so no
    break survives inside a line; anything else control-like is collapsed."""
    lines = [one_line(line, limit=1000) for line in str(text or "").splitlines()] or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["[…truncated]"]
    return ["> " + line if line else ">" for line in lines]


def build_approval_email(authz, links: dict[str, str], requester_name: str, lifetime_days: int) -> tuple[str, str]:
    """(subject, body) for one approver."""
    lines = [
        "A new access authorization is pending your approval:",
        "",
        f"  Name: {one_line(authz.name)}",
        f"  Hosts: {one_line(', '.join(authz.host_ids or [])) or '(none)'}",
        f"  Actions: {one_line(', '.join(authz.actions or [])) or '(none)'}",
    ]
    is_request = getattr(authz, "request_kind", None) == USER_REQUEST
    if is_request:
        # A request's window starts at approval; the stored end date would mislead.
        lines.append(f"  Duration if approved: {int(authz.request_duration_minutes or 0)} minutes from approval")
    else:
        lines.append(f"  Expires if approved: {authz.date_expired.isoformat() if authz.date_expired else 'n/a'}")
    lines.append("")
    if links.get("approve") and links.get("reject"):
        lines += [f"Approve: {links['approve']}", f"Reject: {links['reject']}", ""]
    else:
        lines += [
            "Set 'App Public URL' in Platform Settings to enable one-click approval "
            "links by email. In the meantime, review this request in the SeyalRun "
            "console under Authorizations.", "",
        ]
    lines.append(f"This link expires in {lifetime_days} days and can only be used once.")
    if is_request:
        lines += [
            "",
            f"Reason given by {one_line(requester_name, 100) or 'the requester'} (not verified):",
            *quote_untrusted(authz.reason),
        ]
    return f"Approval needed: {one_line(authz.name, 150)}", "\n".join(lines)
