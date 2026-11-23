"""Small helpers shared by the scanner plugins. Stdlib only."""

from __future__ import annotations

import gzip
import io
import re
from pathlib import Path

from libs.pluginbase import ScanParseError

CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$")
CVE_FIND = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
# A vulnerability id we accept when there is no CVE (DSA-..., GHSA-..., RHSA-...).
VULN_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}-[A-Za-z0-9:._\-]{1,60}$")


def cve(value) -> str | None:
    v = str(value or "").strip().upper()
    return v if CVE_RE.match(v) else None


def find_cves(text) -> list[str]:
    out: list[str] = []
    for m in CVE_FIND.findall(str(text or "")):
        c = m.upper()
        if c not in out:
            out.append(c)
    return out


def clip(value, n: int) -> str:
    return str(value if value is not None else "").strip()[:n]


def open_binary(path: str | Path):
    """Open an upload, transparently gunzipping a .gz (detected by magic bytes,
    not by the name an admin happened to give it)."""
    f = open(path, "rb")
    head = f.read(2)
    f.seek(0)
    if head == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=f, mode="rb")
    return f


def open_text(path: str | Path):
    raw = open_binary(path)
    return io.TextIOWrapper(raw, encoding="utf-8-sig", errors="strict", newline="")


def sniff(path: str | Path, n: int = 4096) -> str:
    """First non-blank characters of the (decompressed) file, for format detection."""
    try:
        with open_binary(path) as f:
            head = f.read(n)
    except (OSError, EOFError) as exc:
        raise ScanParseError(f"cannot read the uploaded file ({exc})") from exc
    try:
        return head.decode("utf-8-sig", errors="replace").lstrip()
    except Exception:  # noqa: BLE001
        return ""
