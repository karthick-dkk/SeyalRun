#!/usr/bin/env python3
"""Build and sign an offline SeyalRun feed bundle (dev / air-gap transfer tool).

Runs on the machine that HAS the feed files (downloaded elsewhere, or the small
fixtures next to this script); the result is one file you carry across the air
gap and upload in Posture -> Feeds -> Import. Needs only Python 3.9+ and the
`cryptography` package. It makes no network calls.

    # 1. once: a signing key pair (keep the .key offline; give the .pub to SeyalRun)
    python3 build_feed_bundle.py keygen --name feeds-2026 --out-dir ./keys
    #    prints:  POSTURE_FEED_TRUSTED_KEYS=feeds-2026:<base64>   -> put in .env

    # 2. each feed refresh
    python3 build_feed_bundle.py build --key ./keys/feeds-2026.key --version 2026.11.22-1 \\
        --osv debian-12.jsonl --osv rocky-9.zip --epss epss_scores.csv.gz \\
        --kev known_exploited_vulnerabilities.json --oval rhel-9.oval.xml.bz2 \\
        --out seyalrun-feeds-2026.11.22-1.tar.gz

    # dev shortcut: key (if missing) + bundle from tools/fixtures
    python3 build_feed_bundle.py sample --out-dir /tmp/feeds

Bundle layout (what posture-service/app/feedbundle.py verifies): manifest.json,
manifest.json.sig (base64 Ed25519 over the exact manifest bytes) and
<source>/<file> for each input, each pinned by sha256 + size in the manifest.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCES = ("osv", "epss", "kev", "oval")


def generate_keypair() -> tuple[bytes, str]:
    """(PEM PKCS8 private key, base64 raw public key)."""
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pem, base64.b64encode(pub).decode()


def load_private_key(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("signing key must be an Ed25519 private key")
    return key


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_bundle(out: Path, private_key_pem: bytes, version: str, inputs: dict[str, list[Path]],
                 generated_at: str | None = None, extra_members: dict[str, bytes] | None = None,
                 tamper: dict[str, bytes] | None = None) -> dict:
    """Write a signed .tar.gz bundle. Returns the manifest.

    ``extra_members`` / ``tamper`` exist for tests only: add an unsigned file, or
    replace a member's bytes AFTER signing, to prove verification rejects both."""
    key = load_private_key(private_key_pem)
    members: dict[str, bytes] = {}
    sources = []
    for name in SOURCES:
        paths = inputs.get(name) or []
        if not paths:
            continue
        files = []
        for p in paths:
            data = Path(p).read_bytes()
            rel = f"{name}/{Path(p).name}"
            if rel in members:
                raise SystemExit(f"duplicate file name {rel}")
            members[rel] = data
            files.append({"path": rel, "sha256": _sha256(data), "size": len(data)})
        sources.append({"name": name, "files": files})
    if not sources:
        raise SystemExit("no input files given")
    manifest = {
        "format": "seyalrun-feed-bundle",
        "format_version": 1,
        "version": version,
        "generated_at": generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "generator": "seyalrun build_feed_bundle.py",
        "sources": sources,
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode()
    signature = base64.b64encode(key.sign(manifest_bytes))
    members = {"manifest.json": manifest_bytes, "manifest.json.sig": signature, **members,
               **(extra_members or {})}
    members.update(tamper or {})
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tf:
        for rel, data in members.items():
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mtime = int(time.time())
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return manifest


def sample_inputs() -> dict[str, list[Path]]:
    return {
        "osv": sorted(FIXTURES.glob("osv*.jsonl")),
        "epss": sorted(FIXTURES.glob("epss*.csv")),
        "kev": sorted(FIXTURES.glob("kev*.json")),
        "oval": sorted(FIXTURES.glob("*.oval.xml")),
    }


def _write_key(out_dir: Path, name: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    pem, pub = generate_keypair()
    key_path = out_dir / f"{name}.key"
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    (out_dir / f"{name}.pub").write_text(pub + "\n")
    return pub


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("keygen", help="create an Ed25519 signing key pair")
    k.add_argument("--name", required=True)
    k.add_argument("--out-dir", required=True, type=Path)

    b = sub.add_parser("build", help="build and sign a bundle")
    b.add_argument("--key", required=True, type=Path, help="Ed25519 private key (PEM)")
    b.add_argument("--version", required=True)
    b.add_argument("--generated-at", default=None)
    b.add_argument("--out", required=True, type=Path)
    for s in SOURCES:
        b.add_argument(f"--{s}", action="append", type=Path, default=[])

    s = sub.add_parser("sample", help="dev: key (if missing) + bundle from tools/fixtures")
    s.add_argument("--out-dir", required=True, type=Path)
    s.add_argument("--name", default="seyalrun-dev")
    s.add_argument("--version", default=None)

    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        pub = _write_key(a.out_dir, a.name)
        print(f"private key: {a.out_dir / (a.name + '.key')}  (keep offline)")
        print(f"POSTURE_FEED_TRUSTED_KEYS={a.name}:{pub}")
        return 0
    if a.cmd == "build":
        manifest = build_bundle(a.out, a.key.read_bytes(), a.version,
                                {src: getattr(a, src) for src in SOURCES}, a.generated_at)
        print(f"wrote {a.out} (version {manifest['version']}, "
              f"{sum(len(x['files']) for x in manifest['sources'])} files)")
        return 0
    key_path = a.out_dir / f"{a.name}.key"
    if not key_path.exists():
        _write_key(a.out_dir, a.name)
    pub = (a.out_dir / f"{a.name}.pub").read_text().strip()
    version = a.version or datetime.now(timezone.utc).strftime("dev-%Y%m%d-%H%M%S")
    out = a.out_dir / f"seyalrun-feeds-{version}.tar.gz"
    build_bundle(out, key_path.read_bytes(), version, sample_inputs())
    print(f"wrote {out}")
    print(f"POSTURE_FEED_TRUSTED_KEYS={a.name}:{pub}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
