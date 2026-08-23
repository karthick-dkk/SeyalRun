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

# "**" ZDLE then a format byte then a hex/binary type. Matching the header alone
# keeps this cheap enough for the hot output path — no protocol state machine.
_ZMODEM_START = re.compile(r"\*\*\x18[ABC]")

# Eight CANs is the canonical ZMODEM abort; the trailing backspaces clear the
# "**" the sender already echoed, so the operator's prompt is not left littered.
ZMODEM_CANCEL = "\x18" * 8 + "\b" * 10

MODE_BLOCK = "block"
MODE_ALLOW = "allow"


def detect(data: str) -> bool:
    """True if this chunk contains the start of a ZMODEM frame."""
    return bool(_ZMODEM_START.search(data))


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
