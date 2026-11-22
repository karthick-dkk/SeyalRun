"""Offline vulnerability-feed bundles: unpack, verify, parse. No network, ever.

A bundle is ONE file (.tar, .tar.gz or .zip) holding:

    manifest.json        {"format": "seyalrun-feed-bundle", "format_version": 1,
                          "version": "...", "generated_at": "<ISO-8601>",
                          "sources": [{"name": "osv|epss|kev|oval",
                                       "files": [{"path", "sha256", "size"}]}]}
    manifest.json.sig    detached Ed25519 signature over the exact manifest
                         bytes (raw 64 bytes, or base64 of them)
    <every file the manifest lists, and nothing else>

Trust is the signature: the manifest is accepted only if it verifies against a
configured public key, and every data file is accepted only if its sha256 and
size match the signed manifest. An unlisted extra file is refused too, so
nothing in a bundle is unsigned.

Parsing is streaming and bounded so a real OSV/OVAL dump does not have to fit in
memory: OSV is read one advisory at a time (JSON Lines, or a zip of per-advisory
JSON files as OSV publishes them), EPSS row by row, OVAL with a SAX-style expat
parser that refuses DOCTYPE/entity declarations. KEV is small and read whole.

Stdlib + cryptography only, no package-relative imports: the test suite loads
this exact file and round-trips it against tools/build_feed_bundle.py.
"""

from __future__ import annotations

import base64
import binascii
import bz2
import csv
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from xml.parsers import expat

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FORMAT = "seyalrun-feed-bundle"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
SIGNATURE = "manifest.json.sig"
SOURCES = ("osv", "epss", "kev", "oval")
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_WHOLE_JSON_BYTES = 64 * 1024 * 1024     # KEV / a single OSV .json file
MAX_OSV_RECORD_BYTES = 32 * 1024 * 1024     # one advisory (zip member / JSONL line)
MAX_ROWS_PER_ADVISORY = 5000
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+\-]{0,63}$")
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHUNK = 1024 * 1024


class BundleError(ValueError):
    """The bundle is malformed, unsigned, tampered with or over a limit. The
    message is shown to the admin verbatim, so it says which and why."""


# ── trusted keys ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TrustedKey:
    name: str
    key: Ed25519PublicKey
    fingerprint: str

    @property
    def label(self) -> str:
        return f"{self.name} (SHA256:{self.fingerprint})"


def key_fingerprint(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def parse_trusted_keys(spec: str) -> list[TrustedKey]:
    """'name:BASE64RAW32[,name2:...]' (comma or newline separated, '#' comments)."""
    keys: list[TrustedKey] = []
    for entry in re.split(r"[,\n]", spec or ""):
        entry = entry.split("#", 1)[0].strip()
        if not entry:
            continue
        name, sep, b64 = entry.partition(":")
        if not sep or not name.strip() or not b64.strip():
            raise BundleError(f"trusted key entry must be 'name:base64' — got {entry[:40]!r}")
        try:
            raw = base64.b64decode(b64.strip(), validate=True)
            key = Ed25519PublicKey.from_public_bytes(raw)
        except (binascii.Error, ValueError) as exc:
            raise BundleError(f"trusted key {name.strip()!r} is not a base64 32-byte Ed25519 public key") from exc
        keys.append(TrustedKey(name.strip(), key, key_fingerprint(raw)))
    return keys


def load_trusted_keys(spec: str, file_path: str = "") -> list[TrustedKey]:
    keys = parse_trusted_keys(spec)
    if file_path:
        keys += parse_trusted_keys(Path(file_path).read_text())
    return keys


# ── unpack ────────────────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or any(part in ("..", "") for part in p.parts) or not p.parts:
        raise BundleError(f"unsafe path in bundle: {name!r}")
    clean = str(p)
    clean = clean.removeprefix("./")
    return clean


def unpack_bundle(bundle: Path, dest: Path, *, max_unpacked: int, max_members: int) -> list[str]:
    """Extract every regular file to ``dest`` (which must be empty), refusing path
    traversal, links, devices and anything over the size/member limits. Returns
    the relative member names."""
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    names: list[str] = []

    def _copy(src, rel: str) -> None:
        nonlocal total
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as out:
            while True:
                buf = src.read(_CHUNK)
                if not buf:
                    break
                total += len(buf)
                if total > max_unpacked:
                    raise BundleError(f"bundle unpacks to more than {max_unpacked} bytes")
                out.write(buf)

    if zipfile.is_zipfile(bundle):
        with zipfile.ZipFile(bundle) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > max_members:
                raise BundleError(f"bundle has more than {max_members} files")
            for info in infos:
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise BundleError(f"symlink in bundle: {info.filename!r}")
                rel = _safe_name(info.filename)
                with zf.open(info) as src:
                    _copy(src, rel)
                names.append(rel)
        return names

    try:
        tf = tarfile.open(bundle, mode="r:*")
    except tarfile.TarError as exc:
        raise BundleError("not a .tar, .tar.gz or .zip bundle") from exc
    with tf:
        count = 0
        for member in tf:
            if member.isdir():
                continue
            count += 1
            if count > max_members:
                raise BundleError(f"bundle has more than {max_members} files")
            if not member.isfile():
                raise BundleError(f"bundle member is not a regular file: {member.name!r}")
            rel = _safe_name(member.name)
            src = tf.extractfile(member)
            if src is None:
                raise BundleError(f"cannot read bundle member {member.name!r}")
            with src:
                _copy(src, rel)
            names.append(rel)
    return names


# ── verify ────────────────────────────────────────────────────────────────────

@dataclass
class VerifiedBundle:
    manifest: dict
    manifest_sha256: str
    signed_by: str
    version: str
    generated_at: datetime | None
    files: dict[str, list[Path]] = field(default_factory=dict)   # source -> absolute paths


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            buf = f.read(_CHUNK)
            if not buf:
                break
            h.update(buf)
            size += len(buf)
    return h.hexdigest(), size


def _read_signature(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) == 64:
        return raw
    try:
        sig = base64.b64decode(raw.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BundleError("manifest.json.sig is neither a raw nor a base64 Ed25519 signature") from exc
    if len(sig) != 64:
        raise BundleError("manifest.json.sig is not a 64-byte Ed25519 signature")
    return sig


def verify_signature(manifest_bytes: bytes, signature: bytes, keys: list[TrustedKey]) -> TrustedKey:
    if not keys:
        raise BundleError("no trusted feed signing keys are configured (POSTURE_FEED_TRUSTED_KEYS); "
                          "refusing to import an unverifiable bundle")
    for k in keys:
        try:
            k.key.verify(signature, manifest_bytes)
            return k
        except InvalidSignature:
            continue
    raise BundleError("bundle signature does not verify against any trusted key "
                      f"({', '.join(k.label for k in keys)})")


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleError(f"manifest generated_at is not ISO-8601: {value!r}") from exc


def verify_bundle(root: Path, names: list[str], keys: list[TrustedKey]) -> VerifiedBundle:
    present = set(names)
    if MANIFEST not in present or SIGNATURE not in present:
        raise BundleError("bundle must contain manifest.json and manifest.json.sig at its root")
    manifest_path = root / MANIFEST
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise BundleError("manifest.json is too large")
    manifest_bytes = manifest_path.read_bytes()
    signer = verify_signature(manifest_bytes, _read_signature(root / SIGNATURE), keys)

    # Only now, with the manifest proven authentic, is its content trusted.
    try:
        manifest = json.loads(manifest_bytes)
    except ValueError as exc:
        raise BundleError("manifest.json is not valid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BundleError(f"manifest format must be {FORMAT!r}")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise BundleError(f"unsupported bundle format_version {manifest.get('format_version')!r} "
                          f"(this release reads {FORMAT_VERSION})")
    version = str(manifest.get("version") or "")
    if not _VERSION_RE.match(version):
        raise BundleError("manifest version must be 1-64 chars of [A-Za-z0-9._:+-]")
    generated_at = _parse_time(manifest.get("generated_at"))
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise BundleError("manifest lists no sources")

    listed: set[str] = set()
    files: dict[str, list[Path]] = {}
    for src in sources:
        name = (src or {}).get("name")
        if name not in SOURCES:
            raise BundleError(f"unknown source {name!r} (expected one of {', '.join(SOURCES)})")
        if name in files:
            raise BundleError(f"source {name!r} listed twice")
        entries = src.get("files")
        if not isinstance(entries, list) or not entries:
            raise BundleError(f"source {name!r} lists no files")
        files[name] = []
        for entry in entries:
            rel = _safe_name(str((entry or {}).get("path") or ""))
            want = str(entry.get("sha256") or "").lower()
            if not _SHA256_RE.match(want):
                raise BundleError(f"{rel}: manifest sha256 is missing or malformed")
            if rel in (MANIFEST, SIGNATURE) or rel in listed:
                raise BundleError(f"{rel}: listed twice or shadows the manifest")
            if rel not in present:
                raise BundleError(f"{rel}: listed in the manifest but missing from the bundle")
            got, size = sha256_file(root / rel)
            if got != want:
                raise BundleError(f"{rel}: sha256 mismatch (manifest {want[:12]}…, file {got[:12]}…) — "
                                  "the bundle was altered after signing")
            if "size" in entry and int(entry["size"]) != size:
                raise BundleError(f"{rel}: size mismatch (manifest {entry['size']}, file {size})")
            listed.add(rel)
            files[name].append(root / rel)
    extra = sorted(present - listed - {MANIFEST, SIGNATURE})
    if extra:
        raise BundleError(f"bundle contains files the manifest does not sign: {', '.join(extra[:5])}")
    return VerifiedBundle(
        manifest=manifest, manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        signed_by=signer.label, version=version, generated_at=generated_at, files=files,
    )


# ── parse (streaming) ─────────────────────────────────────────────────────────

def _open(path: Path):
    """Binary stream, transparently decompressing .gz / .bz2."""
    s = path.name.lower()
    if s.endswith(".gz"):
        return gzip.open(path, "rb")
    if s.endswith(".bz2"):
        return bz2.open(path, "rb")
    return open(path, "rb")


def _base(path: Path) -> str:
    s = path.name.lower()
    for ext in (".gz", ".bz2"):
        if s.endswith(ext):
            return s[: -len(ext)]
    return s


def _cve(value) -> str | None:
    v = str(value or "").strip().upper()
    return v if _CVE_RE.match(v) else None


def _bounded_read(stream, limit: int, what: str) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise BundleError(f"{what} exceeds {limit} bytes")
    return data


# OSV ─────────────────────────────────────────────────────────────────────────

def osv_rows(adv: dict) -> list[dict]:
    """One advisory -> rows of (cve, ecosystem, package, range). Exposed for tests."""
    if not isinstance(adv, dict) or not adv.get("id"):
        raise BundleError("OSV record without an id")
    vid = str(adv["id"])[:128]
    cves: list[str | None] = []
    for cand in [vid, *(adv.get("aliases") or []), *(adv.get("upstream") or [])]:
        c = _cve(cand)
        if c and c not in cves:
            cves.append(c)
    if not cves:
        cves = [None]
    sev = (adv.get("database_specific") or {}).get("severity")
    modified = str(adv.get("modified") or "")[:40] or None
    rows: list[dict] = []

    def emit(eco: str, pkg: str, rtype: str, intro, fixed, last) -> None:
        for c in cves:
            rows.append({"vuln_id": vid, "cve_id": c, "ecosystem": eco[:64], "package": pkg[:255],
                         "range_type": rtype[:16], "introduced": intro, "fixed": fixed,
                         "last_affected": last, "severity": (str(sev)[:32] if sev else None),
                         "modified": modified})

    for aff in adv.get("affected") or []:
        pkg = aff.get("package") or {}
        eco, name = str(pkg.get("ecosystem") or ""), str(pkg.get("name") or "")
        if not name:
            continue
        before = len(rows)
        for rg in aff.get("ranges") or []:
            rtype = str(rg.get("type") or "")
            if rtype == "GIT":
                continue
            intro = None
            for ev in rg.get("events") or []:
                if "introduced" in ev:
                    if intro is not None:
                        emit(eco, name, rtype, intro, None, None)
                    intro = str(ev["introduced"])[:255]
                elif "fixed" in ev or "last_affected" in ev:
                    emit(eco, name, rtype, intro, str(ev.get("fixed"))[:255] if "fixed" in ev else None,
                         str(ev.get("last_affected"))[:255] if "last_affected" in ev else None)
                    intro = None
            if intro is not None:
                emit(eco, name, rtype, intro, None, None)
        if len(rows) == before and aff.get("versions"):
            emit(eco, name, "VERSIONS", None, None, None)
        if len(rows) > MAX_ROWS_PER_ADVISORY:
            raise BundleError(f"OSV advisory {vid} expands to more than {MAX_ROWS_PER_ADVISORY} rows")
    return rows


def _osv_records(path: Path) -> Iterator[dict]:
    base = _base(path)
    if base.endswith(".zip"):
        if path.name.lower() != base:
            raise BundleError(f"{path.name}: a compressed .zip is not supported; ship the .zip itself")
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.lower().endswith(".json"):
                    continue
                with zf.open(info) as f:
                    yield _json(_bounded_read(f, MAX_OSV_RECORD_BYTES, info.filename), info.filename)
    elif base.endswith((".jsonl", ".ndjson")):
        with _open(path) as f:
            for n, line in enumerate(f, 1):
                if len(line) > MAX_OSV_RECORD_BYTES:
                    raise BundleError(f"{path.name}:{n}: OSV record exceeds {MAX_OSV_RECORD_BYTES} bytes")
                if line.strip():
                    yield _json(line, f"{path.name}:{n}")
    elif base.endswith(".json"):
        with _open(path) as f:
            doc = _json(_bounded_read(f, MAX_WHOLE_JSON_BYTES, path.name), path.name)
        yield from (doc if isinstance(doc, list) else [doc])
    else:
        raise BundleError(f"{path.name}: OSV files must be .jsonl, .json or .zip (optionally .gz/.bz2)")


def _json(raw: bytes, where: str):
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise BundleError(f"{where}: invalid JSON") from exc


def iter_osv(path: Path, stats: dict) -> Iterator[dict]:
    for adv in _osv_records(path):
        stats["entries"] = stats.get("entries", 0) + 1
        yield from osv_rows(adv)


# EPSS ────────────────────────────────────────────────────────────────────────

def iter_epss(path: Path, stats: dict) -> Iterator[dict]:
    """FIRST.org EPSS csv: optional '#model_version:...,score_date:...' line, then
    'cve,epss,percentile'."""
    with _open(path) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        header: list[str] | None = None
        for n, row in enumerate(csv.reader(text), 1):
            if not row or not "".join(row).strip():
                continue
            if row[0].startswith("#"):
                meta = ",".join(row).lstrip("#")
                for part in meta.split(","):
                    k, _, v = part.partition(":")
                    if k.strip() in ("model_version", "score_date"):
                        stats[k.strip()] = v.strip()
                continue
            if header is None:
                header = [h.strip().lower() for h in row]
                if not {"cve", "epss", "percentile"} <= set(header):
                    raise BundleError(f"{path.name}: EPSS header must include cve,epss,percentile")
                ix = {h: i for i, h in enumerate(header)}
                continue
            try:
                cve = _cve(row[ix["cve"]])
                score, pct = float(row[ix["epss"]]), float(row[ix["percentile"]])
            except (IndexError, ValueError) as exc:
                raise BundleError(f"{path.name}:{n}: malformed EPSS row") from exc
            if not cve or not (0.0 <= score <= 1.0 and 0.0 <= pct <= 1.0):
                raise BundleError(f"{path.name}:{n}: invalid EPSS row {row[:3]}")
            stats["entries"] = stats.get("entries", 0) + 1
            yield {"cve_id": cve, "score": score, "percentile": pct}
        if header is None:
            raise BundleError(f"{path.name}: empty EPSS file")


# KEV ─────────────────────────────────────────────────────────────────────────

def iter_kev(path: Path, stats: dict) -> Iterator[dict]:
    """CISA Known Exploited Vulnerabilities catalog JSON."""
    with _open(path) as f:
        doc = _json(_bounded_read(f, MAX_WHOLE_JSON_BYTES, path.name), path.name)
    vulns = doc.get("vulnerabilities") if isinstance(doc, dict) else None
    if not isinstance(vulns, list):
        raise BundleError(f"{path.name}: KEV catalog has no 'vulnerabilities' list")
    if doc.get("catalogVersion"):
        stats["catalog_version"] = str(doc["catalogVersion"])[:40]
    for v in vulns:
        cve = _cve((v or {}).get("cveID"))
        if not cve:
            raise BundleError(f"{path.name}: KEV entry without a valid cveID: {str(v)[:80]}")
        stats["entries"] = stats.get("entries", 0) + 1
        yield {
            "cve_id": cve,
            "date_added": str(v.get("dateAdded") or "")[:10] or None,
            "due_date": str(v.get("dueDate") or "")[:10] or None,
            "ransomware": str(v.get("knownRansomwareCampaignUse") or "").lower() == "known",
            "vendor": str(v.get("vendorProject") or "")[:255],
            "product": str(v.get("product") or "")[:255],
        }


# OVAL ────────────────────────────────────────────────────────────────────────

_OVAL_CRITERION = [
    # Red Hat: "openssl is earlier than 1:3.0.7-25.el9_3"; Debian: "openssl DPKG is earlier than 3.0.11-1~deb12u2"
    re.compile(r"^(?P<pkg>[A-Za-z0-9][\w.+\-]*)(?: DPKG)? is earlier than (?P<ver>\S+)$"),
    # Ubuntu: "openssl package in jammy was vulnerable but has been fixed (note: '3.0.2-0ubuntu1.12')."
    re.compile(r"^(?P<pkg>[A-Za-z0-9][\w.+\-]*) package in \S+ (?:was vulnerable but )?has been fixed "
               r"\(note: '(?P<ver>[^']+)'\)"),
]


def oval_criterion(comment: str) -> tuple[str, str] | None:
    for rx in _OVAL_CRITERION:
        m = rx.match((comment or "").strip())
        if m:
            return m.group("pkg"), m.group("ver")
    return None


def iter_oval(path: Path, stats: dict) -> Iterator[dict]:
    """Patch definitions -> (definition, cve, platform, package, fixed version).

    expat directly rather than ElementTree: it streams, and it lets DOCTYPE and
    entity declarations be refused outright (no billion-laughs, no external
    entities) without a third-party XML library."""
    parser = expat.ParserCreate(namespace_separator=" ")
    out: list[dict] = []
    state: dict = {"def": None, "text": None, "tag": None}

    def _deny(*_a):
        raise BundleError(f"{path.name}: DOCTYPE/entity declarations are not allowed in OVAL")

    def start(name, attrs):
        local = name.rsplit(" ", 1)[-1]
        d = state["def"]
        if local == "definition":
            state["def"] = {"id": str(attrs.get("id", ""))[:128], "cves": [], "platforms": [],
                            "severity": None, "pkgs": []}
        elif d is None:
            return
        elif local == "reference" and attrs.get("source", "").upper() == "CVE":
            c = _cve(attrs.get("ref_id"))
            if c and c not in d["cves"]:
                d["cves"].append(c)
        elif local in ("cve", "platform", "severity"):
            state["tag"], state["text"] = local, []
        elif local == "criterion":
            hit = oval_criterion(attrs.get("comment", ""))
            if hit and hit not in d["pkgs"]:
                d["pkgs"].append(hit)

    def chars(data):
        if state["text"] is not None:
            state["text"].append(data)

    def end(name):
        local = name.rsplit(" ", 1)[-1]
        d = state["def"]
        if d is None:
            return
        if state["tag"] == local and state["text"] is not None:
            val = "".join(state["text"]).strip()
            if local == "cve":
                c = _cve(val)
                if c and c not in d["cves"]:
                    d["cves"].append(c)
            elif local == "platform" and val:
                d["platforms"].append(val)
            elif local == "severity" and val:
                d["severity"] = val[:32]
            state["tag"], state["text"] = None, None
        if local == "definition":
            stats["entries"] = stats.get("entries", 0) + 1
            platform = "; ".join(d["platforms"])[:255]
            for pkg, ver in d["pkgs"]:
                for c in d["cves"] or [None]:
                    out.append({"definition_id": d["id"], "cve_id": c, "platform": platform,
                                "package": pkg[:255], "fixed_version": ver[:255], "severity": d["severity"]})
            state["def"] = None

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.StartDoctypeDeclHandler = _deny
    parser.EntityDeclHandler = _deny
    with _open(path) as f:
        while True:
            chunk = f.read(_CHUNK)
            try:
                parser.Parse(chunk, not chunk)
            except expat.ExpatError as exc:
                raise BundleError(f"{path.name}: malformed XML ({exc})") from exc
            if out:
                yield from out
                out.clear()
            if not chunk:
                break


PARSERS: dict[str, Callable[[Path, dict], Iterator[dict]]] = {
    "osv": iter_osv, "epss": iter_epss, "kev": iter_kev, "oval": iter_oval,
}


def iter_source(source: str, paths: list[Path], stats: dict) -> Iterator[dict]:
    for p in paths:
        yield from PARSERS[source](p, stats)


def take(it: Iterator[dict], n: int) -> list[dict]:
    """Next n rows (fewer at the end). Called via asyncio.to_thread so parsing
    never blocks the event loop."""
    out = []
    for row in it:
        out.append(row)
        if len(out) >= n:
            break
    return out


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def free_bytes(path: Path) -> int:
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except OSError:
        return -1
