"""ZMODEM (rz/sz) detection and policy for the terminal stream.

ZMODEM is a file transfer that runs *inside* the interactive session. That makes
it a second transfer channel, parallel to SFTP and subject to none of its
controls: it does not go through the `upload`/`download` grants, it does not land
in the SFTP drop point, and it writes no sftp.* audit row. A PAM that ships SFTP
with a /tmp jail and an audit trail, and then leaves ZMODEM open, has an audited
front door beside an unaudited one.

So it is a *gate*, not a transport. Detection sits in the output path; policy
decides. Default is `block`:

  block  cancel the transfer, tell the operator, audit the attempt (default)
  allow  pass it through, still audited — for deployments that need rz/sz and
         accept that those bytes are outside the SFTP controls

Detection works on the ZMODEM framing rather than on the command line, so it
catches `sz`/`rz` however they were invoked — through an alias, a script, a
wrapper — because what is detected is the protocol starting, not a word typed.

Frame headers (ZMODEM, Forsberg): a frame begins ZPAD ZPAD ZDLE, i.e. "**\\x18",
followed by a format byte: 'A' (ZBIN), 'B' (ZHEX), 'C' (ZBIN32). The frame type
follows. ZRQINIT (00) opens a receive; ZRINIT (01) answers it.
"""

from __future__ import annotations

import re

# "**" ZDLE then a format byte then the frame type. Matching the header alone
# keeps this cheap enough for the hot output path — no protocol state machine.
_ZMODEM_START = re.compile(r"\*\*\x18[ABC]")

# ZHEX frames carry the type as two hex characters after the 'B'. The two that
# open a transfer are the only ones that need classifying:
#   ZRQINIT (00) — the remote is asking to SEND, i.e. `sz`: bytes flow host to
#                  browser, which is a DOWNLOAD and needs the `download` grant.
#   ZRINIT  (01) — the remote is ready to RECEIVE, i.e. `rz`: bytes flow browser
#                  to host, an UPLOAD, needing `upload`.
# Reading the direction off the protocol rather than off the command line is what
# makes the grant check correct for an aliased or wrapped invocation.
_ZHEX_TYPE = re.compile(r"\*\*\x18B([0-9a-fA-F]{2})")

DIR_DOWNLOAD = "download"
DIR_UPLOAD = "upload"

# Eight CANs is the canonical ZMODEM abort; the trailing backspaces clear the
# "**" the sender already echoed, so the operator's prompt is not left littered.
ZMODEM_CANCEL = "\x18" * 8 + "\b" * 10

MODE_BLOCK = "block"
MODE_ALLOW = "allow"


# ZFIN (type 08) closes a session in either direction; "OO" ("over and out") is
# the sender's final ack. Either marks the point after which frames are ordinary
# terminal output again.
_ZFIN = re.compile(r"\*\*\x18B08|OO\x08*$")


def is_finished(data: str) -> bool:
    """True if this chunk ends the transfer.

    Used only to stop treating subsequent frames as part of an authorized
    transfer. Missing it is harmless — the next opening frame is policed again
    regardless — so this errs toward ending the window early rather than holding
    it open.
    """
    return bool(_ZFIN.search(data))


def notice_denied(what: str, action: str) -> str:
    """Shown when the transport is enabled but this direction is not granted."""
    return (
        f"\r\n\x1b[33m[SeyalRun]\x1b[0m ZMODEM {what} refused — the `{action}` "
        f"permission is not granted for this host.\r\n"
    )


def detect(data: str) -> bool:
    """True if this chunk contains the start of a ZMODEM frame."""
    return bool(_ZMODEM_START.search(data))


def direction(data: str) -> str | None:
    """Which grant this transfer needs, or None if the frame does not say.

    Only ZHEX opening frames are classified. A binary (ZBIN/ZBIN32) opener or a
    mid-transfer frame returns None, and callers must treat that as "unknown" —
    which is refused when a policy decision is required, because guessing the
    direction would mean guessing which permission to check.
    """
    m = _ZHEX_TYPE.search(data)
    if not m:
        return None
    return {"00": DIR_DOWNLOAD, "01": DIR_UPLOAD}.get(m.group(1).lower())


def notice(mode: str) -> str:
    """What the operator sees in their terminal when a transfer is refused.

    Written to be actionable rather than merely obstructive: someone reaching for
    sz/rz wants to move a file, and the product has a supported way to do that.
    """
    if mode != MODE_BLOCK:
        return ""
    return (
        "\r\n\x1b[33m[SeyalRun]\x1b[0m ZMODEM transfer blocked — rz/sz bypasses the "
        "audited file-transfer path.\r\n"
        "           Use the \x1b[1mFiles\x1b[0m panel (top right) to upload or download; "
        "it is authorized per host and recorded.\r\n"
    )
