"""Parse the output of automation-service's posture_facts job into host facts.

The other half of the contract is automation-service/app/plugins/executors/
posture_facts.sh. Stdlib only and free of package-relative imports so the
test suite loads this exact file (core/tests/test_posture_facts.py) and runs it
against real Debian- and RHEL-family output.

Job output arrives as the run's output_lines: every remote line is prefixed
"[host:<id>] " by the bash executor, lines may have been masked (a registered
secret replaced with ****), and only the last few hundred are kept. The payload
is gzip+base64 framed between markers, so masking or truncation shows up as a
decode error here rather than as quietly wrong facts.
"""

from __future__ import annotations

import base64
import binascii
import re
import zlib

BEGIN = "SEYALRUN-FACTS-BEGIN"
END = "SEYALRUN-FACTS-END"
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024   # decompressed; a host with 20k packages is ~2 MB
# posture_facts.sh ends every warning about a failed update listing with this. Such a
# host has an empty update list that means "unknown", not "up to date".
UPDATES_UNKNOWN = "pending updates unknown"


def updates_known(warnings: list | None) -> bool:
    return not any(UPDATES_UNKNOWN in str(w) for w in warnings or [])

_HOST_PREFIX = re.compile(r"^\[host:[^\]]*\] ?")
_APT_LIST = re.compile(
    r"^(?P<name>[^/\s]+)/(?P<suites>\S+)\s+(?P<ver>\S+)\s+(?P<arch>\S+)"
    r"(?:\s+\[upgradable from: (?P<cur>[^\]]+)\])?"
)
_APT_SIM = re.compile(r"^Inst (?P<name>\S+) (?:\[(?P<cur>[^\]]+)\] )?\((?P<ver>\S+) (?P<src>[^\[)]*)")
_KERNEL_PKG = re.compile(r"^linux-image-(\d[\w.+~-]*)$")

DEBIAN_IDS = {"debian", "ubuntu", "linuxmint", "raspbian", "kali", "pop", "elementary", "devuan"}
RHEL_IDS = {"rhel", "centos", "rocky", "almalinux", "ol", "fedora", "amzn", "scientific",
            "eurolinux", "circle", "navix", "cloudlinux", "virtuozzo"}


class FactsError(ValueError):
    """The job output did not contain a usable facts payload."""


def strip_prefix(line: str) -> str:
    return _HOST_PREFIX.sub("", line, count=1)


def extract_payload(output_lines: list[str]) -> str:
    """The decoded record text between the last complete BEGIN/END pair."""
    chunks: list[str] | None = None
    encoding = ""
    complete: tuple[str, list[str]] | None = None
    for raw in output_lines or []:
        line = strip_prefix(str(raw)).strip()
        if line.startswith(BEGIN):
            parts = line.split()
            if len(parts) < 3 or parts[1] != "v1":
                raise FactsError(f"unsupported facts payload header: {line[:80]!r}")
            encoding, chunks = parts[2], []
            continue
        if line.startswith(END):
            if chunks is not None:
                complete = (encoding, chunks)
            chunks = None
            continue
        if chunks is not None and line:
            chunks.append(line)
    if complete is None:
        raise FactsError("no complete facts payload in job output (collection failed or output truncated)")
    encoding, chunks = complete
    try:
        data = base64.b64decode("".join(chunks), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FactsError("facts payload is not valid base64 (output was altered in transit)") from exc
    if encoding == "gz":
        d = zlib.decompressobj(wbits=31)
        try:
            data = d.decompress(data, MAX_PAYLOAD_BYTES)
        except zlib.error as exc:
            raise FactsError("facts payload failed its gzip integrity check") from exc
        if d.unconsumed_tail:
            raise FactsError("facts payload exceeds the size limit")
    elif encoding != "raw":
        raise FactsError(f"unknown facts payload encoding {encoding!r}")
    text = data.decode("utf-8", "replace")
    if "\nZ\tend" not in "\n" + text:
        raise FactsError("facts payload is incomplete (no end sentinel)")
    return text


def _os_release(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


SUSE_IDS = {"suse", "opensuse", "sles", "sled", "opensuse-leap", "opensuse-tumbleweed", "sle_hpc", "sles_sap"}


def distro_family(os_id: str, id_like: str, legacy: str = "") -> str:
    ids = {os_id.lower()} | set(id_like.lower().split())
    if ids & DEBIAN_IDS or "debian" in ids:
        return "debian"
    if ids & RHEL_IDS or {"rhel", "fedora", "centos"} & ids:
        return "rhel"
    if ids & SUSE_IDS:
        return "suse"
    if legacy and re.search(r"red hat|centos|rocky|alma|oracle", legacy, re.IGNORECASE):
        return "rhel"
    return ""


def _natural_key(s: str) -> list:
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s)]


def _split_nevra(nevra: str) -> tuple[str, str]:
    """'openssl-libs-1:3.0.7-25.el9_3.x86_64' -> ('openssl-libs', 'x86_64')."""
    base, _, arch = nevra.rpartition(".")
    if not base:
        return nevra, ""
    parts = base.rsplit("-", 2)
    return (parts[0] if len(parts) == 3 else base), arch


def _parse_check_update(lines: list[str]) -> list[tuple[str, str, str, str]]:
    """dnf/yum check-update rows -> (name, arch, version, repo). yum wraps a long
    package name onto its own line, so a lone token is joined to the next."""
    rows = []
    pending = ""
    for line in lines:
        if line.startswith(("Obsoleting", "Security:", "Last metadata", "Loaded plugins")):
            if line.startswith("Obsoleting"):
                break
            continue
        tokens = (pending + " " + line).split() if pending else line.split()
        pending = ""
        if len(tokens) == 1:
            pending = tokens[0]
            continue
        if len(tokens) < 2 or "." not in tokens[0]:
            continue
        name, _, arch = tokens[0].rpartition(".")
        rows.append((name, arch, tokens[1], tokens[2] if len(tokens) > 2 else ""))
    return rows


def parse_facts(text: str) -> dict:
    """Record text -> facts dict (see posture_facts.sh for the record types)."""
    rec: dict[str, list[list[str]]] = {}
    for line in text.splitlines():
        if not line or "\t" not in line:
            continue
        kind, _, rest = line.partition("\t")
        rec.setdefault(kind, []).append(rest.split("\t"))

    def first(kind: str, default: str = "") -> str:
        vals = rec.get(kind) or []
        return vals[0][0].strip() if vals and vals[0] else default

    os_rel = _os_release(["\t".join(r) for r in rec.get("R", [])])
    legacy = first("L")
    os_id = os_rel.get("ID", "")
    family = distro_family(os_id, os_rel.get("ID_LIKE", ""), legacy)
    kernel = first("K")
    pkg_manager = first("M")
    warnings = ["\t".join(r) for r in rec.get("E", [])]

    installed: list[dict] = []
    for r in rec.get("I", []):
        if len(r) < 2 or not r[0]:
            continue
        status = r[3] if len(r) > 3 else "installed"
        if not status.strip().endswith("installed") or r[0] == "gpg-pubkey":
            continue
        installed.append({"name": r[0], "version": r[1], "arch": r[2] if len(r) > 2 else "",
                          "source": (r[4].strip() if len(r) > 4 else ""),
                          "source_version": (r[5].strip() if len(r) > 5 else "")})
    by_name: dict[str, str] = {}
    by_name_arch: dict[tuple[str, str], str] = {}
    for p in installed:
        by_name.setdefault(p["name"], p["version"])
        by_name_arch[(p["name"], p["arch"])] = p["version"]

    pending: list[dict] = []
    for r in rec.get("P", []):
        m = _APT_LIST.match("\t".join(r))
        if not m:
            continue
        suites = m.group("suites")
        cur = m.group("cur") or by_name_arch.get((m.group("name"), m.group("arch"))) or by_name.get(m.group("name"), "")
        pending.append({"name": m.group("name"), "arch": m.group("arch"), "current": cur,
                        "available": m.group("ver"), "security": "security" in suites.lower(),
                        "source": suites})
    for r in rec.get("S", []):
        m = _APT_SIM.match("\t".join(r))
        if not m:
            continue
        src = m.group("src").strip()
        pending.append({"name": m.group("name"), "arch": "", "current": m.group("cur") or by_name.get(m.group("name"), ""),
                        "available": m.group("ver"), "security": "security" in src.lower(), "source": src})
    if rec.get("C"):
        security_names: set[str] = set()
        for r in rec.get("Q", []):
            toks = "\t".join(r).split()
            if len(toks) >= 3 and re.match(r"^[A-Z]+-\d{4}[:\-]", toks[0]):
                security_names.add(_split_nevra(toks[-1])[0])
        for name, arch, ver, repo in _parse_check_update(["\t".join(r) for r in rec["C"]]):
            pending.append({"name": name, "arch": arch,
                            "current": by_name_arch.get((name, arch)) or by_name.get(name, ""),
                            "available": ver, "security": name in security_names, "source": repo})

    for r in rec.get("W", []):      # v | repository | name | current | available | arch
        cols = [c.strip() for c in "\t".join(r).split("|")]
        if len(cols) >= 6 and cols[2] and cols[4]:
            pending.append({"name": cols[2], "arch": cols[5], "current": cols[3], "available": cols[4],
                            "security": False, "source": cols[1]})

    reboot: bool | None = None
    method = ""
    for r in rec.get("B", []):
        if r and r[0] in ("0", "1"):
            reboot, method = r[0] == "1", (r[1] if len(r) > 1 else "")
    if reboot is None and kernel:
        kernels = rec.get("N", [])
        if kernels:   # rpm: newest installed kernel (by install time) vs running
            newest = max(kernels, key=lambda k: int(k[2]) if len(k) > 2 and k[2].isdigit() else 0)
            if len(newest) > 1:
                reboot, method = newest[1] != kernel, "kernel-compare"
        else:         # dpkg: installed linux-image-<release> packages vs running
            releases = [m.group(1) for p in installed if (m := _KERNEL_PKG.match(p["name"]))]
            if releases:
                newest = max(releases, key=_natural_key)
                reboot, method = (kernel not in releases or newest != kernel), "kernel-compare"

    hostname = first("H")
    return {
        "hostname": hostname,
        "kernel": kernel,
        "arch": first("A"),
        "distro": os_rel.get("NAME") or legacy or os_id,
        "distro_id": os_id,
        "distro_version": os_rel.get("VERSION_ID", ""),
        "distro_pretty": os_rel.get("PRETTY_NAME") or legacy,
        "distro_family": family,
        "pkg_manager": pkg_manager,
        "installed": installed,
        "pending": pending,
        "security_updates": sum(1 for p in pending if p["security"]),
        "reboot_required": reboot,
        "reboot_method": method,
        "warnings": warnings,
        "pending_known": updates_known(warnings),
    }


def last_error_line(output_lines: list[str]) -> str:
    """Best human-readable reason from a failed run's output (e.g. "no credential
    available to connect"), skipping payload chunks and progress chatter."""
    for raw in reversed(output_lines or []):
        line = strip_prefix(str(raw)).strip()
        if not line or line.startswith((BEGIN, END)) or len(line) > 300 and " " not in line:
            continue
        if line.endswith(("fetching host info...", "connecting...", "connecting... (sudo)")):
            continue
        return line[:500]
    return "facts job produced no output"
