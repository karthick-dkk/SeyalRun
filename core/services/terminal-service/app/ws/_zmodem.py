"""ZMODEM (rz/sz) detection — always blocked, always audited.

SFTP is the only file-transfer path in this product, and ZMODEM would be a second
one running inside the interactive session, subject to none of its controls:

    audit record   SFTP logs path + bytes per file; ZMODEM can only report that a
                   transfer happened and in which direction. The filename lives
                   inside the protocol stream, so "what file left this host" —
                   the question an assessor actually asks — is unanswerable.
    destination    SFTP is confined to sftp_root (/tmp). `rz` writes wherever the
                   operator's shell happens to be, so it walks straight around
                   that confinement.
    size limit     SFTP enforces one; passthrough bytes are not counted.
    dependency     ZMODEM needs lrzsz installed on every managed host.

A working ZMODEM transport was built and then removed, deliberately: it was
strictly worse than the SFTP path that already existed, and keeping it as a
config flag would have left a switch that quietly undoes the /tmp confinement.
What remains is the control — detect the protocol starting, cancel it, tell the
operator where the supported path is, and record the attempt.

Detection works on ZMODEM framing rather than on the command line, so it catches
sz/rz however they were invoked — an alias, a script, a wrapper — because what is
detected is the protocol starting, not a word someone typed.

Frame headers (ZMODEM, Forsberg): a frame begins ZPAD ZPAD ZDLE — "**\x18" —
followed by a format byte, 'A' (ZBIN), 'B' (ZHEX) or 'C' (ZBIN32).
"""


from __future__ import annotations

import re

# "**" ZDLE then a format byte then the frame type. Matching the header alone
# keeps this cheap enough for the hot output path — no protocol state machine.
_ZMODEM_START = re.compile(r"\*\*\x18[ABC]")


# Eight CANs is the canonical ZMODEM abort; the trailing backspaces clear the
# "**" the sender already echoed, so the operator's prompt is not left littered.
ZMODEM_CANCEL = "\x18" * 8 + "\b" * 10







def detect(data: str) -> bool:
    """True if this chunk contains the start of a ZMODEM frame."""
    return bool(_ZMODEM_START.search(data))




def notice() -> str:
    """What the operator sees in their terminal when a transfer is refused.

    Written to be actionable rather than merely obstructive: someone reaching for
    sz/rz wants to move a file, and the product has a supported way to do that.
    """
    return (
        "\r\n\x1b[33m[SeyalRun]\x1b[0m ZMODEM transfer blocked — rz/sz bypasses the "
        "audited file-transfer path.\r\n"
        "           Use the \x1b[1mFiles\x1b[0m panel (top right) to upload or download; "
        "it is authorized per host and recorded.\r\n"
    )
