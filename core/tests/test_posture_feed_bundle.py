"""Offline feed bundles: a bundle built and signed by the shipped tool imports;
anything unsigned, re-signed by an untrusted key, altered after signing, or
carrying an unsigned extra file is refused with a reason an admin can act on.

Runs the real tools/build_feed_bundle.py against the real app/feedbundle.py
(both stdlib + cryptography), with the fixture files the tool ships — no
network, no database.
"""

from __future__ import annotations

import base64
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

BUNDLE = "services/posture-service/app/feedbundle.py"
TOOL = "services/posture-service/tools/build_feed_bundle.py"
LIMITS = {"max_unpacked": 64 * 1024 * 1024, "max_members": 100}


def _load_registered(relpath: str, name: str):
    """Like conftest's load_module, but registered in sys.modules first —
    dataclasses resolve their module through it."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent.parent / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fb():
    return _load_registered(BUNDLE, "posture_feedbundle")


@pytest.fixture
def tool():
    return _load_registered(TOOL, "posture_bundle_tool")


@pytest.fixture
def signer(tool, fb):
    pem, pub = tool.generate_keypair()
    return pem, fb.parse_trusted_keys(f"test-key:{pub}")


def _build(tool, tmp_path: Path, pem: bytes, name="b.tar.gz", **kw) -> Path:
    out = tmp_path / name
    tool.build_bundle(out, pem, kw.pop("version", "2026.11.22-1"), kw.pop("inputs", None) or tool.sample_inputs(),
                      generated_at="2026-11-20T00:00:00+00:00", **kw)
    return out


def _verify(fb, bundle: Path, keys, tmp_path: Path):
    work = tmp_path / "work"
    names = fb.unpack_bundle(bundle, work, **LIMITS)
    return fb.verify_bundle(work, names, keys)


def _rows(fb, vb) -> dict[str, tuple[dict, list[dict]]]:
    out = {}
    for source, paths in vb.files.items():
        stats: dict = {}
        out[source] = (stats, list(fb.iter_source(source, paths, stats)))
    return out


def test_signed_sample_bundle_imports_every_source(fb, tool, signer, tmp_path):
    pem, keys = signer
    vb = _verify(fb, _build(tool, tmp_path, pem), keys, tmp_path)
    assert vb.version == "2026.11.22-1" and vb.generated_at.year == 2026
    assert vb.signed_by.startswith("test-key (SHA256:")
    rows = _rows(fb, vb)
    assert set(rows) == {"osv", "epss", "kev", "oval"}

    osv_stats, osv = rows["osv"]
    assert osv_stats["entries"] == 4
    deb = [r for r in osv if r["ecosystem"] == "Debian:12" and r["package"] == "openssl"]
    assert deb and deb[0]["cve_id"] == "CVE-2023-5678" and deb[0]["fixed"] == "3.0.13-1~deb12u1"
    # one advisory naming two CVEs yields a row per CVE, so either CVE finds it
    assert {r["cve_id"] for r in osv if r["vuln_id"] == "DSA-5600-1"} == {"CVE-2024-0567", "CVE-2024-0553"}
    rocky = {r["package"] for r in osv if r["ecosystem"] == "Rocky Linux:9"}
    assert rocky == {"openssl", "openssl-libs"}

    epss_stats, epss = rows["epss"]
    assert epss_stats["entries"] == 5 and epss_stats["model_version"] == "v2025.03.14"
    glibc = next(r for r in epss if r["cve_id"] == "CVE-2024-2961")
    assert glibc["score"] == pytest.approx(0.89211) and glibc["percentile"] == pytest.approx(0.99412)

    kev_stats, kev = rows["kev"]
    assert kev_stats["entries"] == 2
    log4j = next(r for r in kev if r["cve_id"] == "CVE-2021-44228")
    assert log4j["ransomware"] is True and log4j["date_added"] == "2021-12-10"

    oval_stats, oval = rows["oval"]
    assert oval_stats["entries"] == 2
    got = {(r["package"], r["fixed_version"], r["cve_id"]) for r in oval}
    assert got == {("openssl", "1:3.0.7-25.el9_3", "CVE-2023-5678"),
                   ("openssl-libs", "1:3.0.7-25.el9_3", "CVE-2023-5678"),
                   ("gnutls28", "3.7.9-2+deb12u2", "CVE-2024-0553")}
    assert all("signed with" not in r["package"] for r in oval)


def test_zip_bundle_is_accepted(fb, tool, signer, tmp_path):
    """Same content as a .zip — the other container format the spec allows."""
    pem, keys = signer
    tgz = _build(tool, tmp_path, pem)
    zpath = tmp_path / "b.zip"
    with tarfile.open(tgz) as tf, zipfile.ZipFile(zpath, "w") as zf:
        for m in tf.getmembers():
            zf.writestr(m.name, tf.extractfile(m).read())
    assert _verify(fb, zpath, keys, tmp_path).version == "2026.11.22-1"


def test_untrusted_signer_is_refused(fb, tool, signer, tmp_path):
    _, keys = signer
    other_pem, _ = tool.generate_keypair()
    with pytest.raises(fb.BundleError, match="does not verify against any trusted key"):
        _verify(fb, _build(tool, tmp_path, other_pem), keys, tmp_path)


def test_any_of_several_trusted_keys_verifies(fb, tool, tmp_path):
    """Key rotation: old and new key both trusted during the overlap."""
    old_pem, old_pub = tool.generate_keypair()
    _, new_pub = tool.generate_keypair()
    keys = fb.parse_trusted_keys(f"new:{new_pub},\nold:{old_pub}")
    assert _verify(fb, _build(tool, tmp_path, old_pem), keys, tmp_path).signed_by.startswith("old ")


def test_no_trusted_keys_refuses_everything(fb, tool, signer, tmp_path):
    pem, _ = signer
    with pytest.raises(fb.BundleError, match="no trusted feed signing keys"):
        _verify(fb, _build(tool, tmp_path, pem), [], tmp_path)


def test_file_altered_after_signing_is_refused(fb, tool, signer, tmp_path):
    pem, keys = signer
    bad = b"cve,epss,percentile\nCVE-2021-44228,0.00001,0.00001\n"
    bundle = _build(tool, tmp_path, pem, tamper={"epss/epss-sample.csv": bad})
    with pytest.raises(fb.BundleError, match="sha256 mismatch"):
        _verify(fb, bundle, keys, tmp_path)


def test_manifest_altered_after_signing_is_refused(fb, tool, signer, tmp_path):
    pem, keys = signer
    bundle = _build(tool, tmp_path, pem)
    with tarfile.open(bundle) as tf:
        members = {m.name: tf.extractfile(m).read() for m in tf.getmembers()}
    manifest = json.loads(members["manifest.json"])
    manifest["version"] = "9999.1"
    bundle2 = _build(tool, tmp_path, pem, name="b2.tar.gz",
                     tamper={"manifest.json": json.dumps(manifest).encode()})
    with pytest.raises(fb.BundleError, match="signature"):
        _verify(fb, bundle2, keys, tmp_path)


def test_unsigned_extra_file_is_refused(fb, tool, signer, tmp_path):
    pem, keys = signer
    bundle = _build(tool, tmp_path, pem, extra_members={"osv/injected.jsonl": b'{"id":"X"}\n'})
    with pytest.raises(fb.BundleError, match="does not sign"):
        _verify(fb, bundle, keys, tmp_path)


def test_path_traversal_member_is_refused(fb, tmp_path):
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tf:
        info = tarfile.TarInfo("../../etc/cron.d/x")
        data = b"pwned"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with pytest.raises(fb.BundleError, match="unsafe path"):
        fb.unpack_bundle(evil, tmp_path / "w", **LIMITS)
    assert not (tmp_path.parent.parent / "etc" / "cron.d" / "x").exists()


def test_symlink_member_is_refused(fb, tmp_path):
    evil = tmp_path / "link.tar"
    with tarfile.open(evil, "w") as tf:
        info = tarfile.TarInfo("manifest.json")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(fb.BundleError, match="not a regular file"):
        fb.unpack_bundle(evil, tmp_path / "w", **LIMITS)


def test_unpacked_size_limit(fb, tool, signer, tmp_path):
    pem, _ = signer
    with pytest.raises(fb.BundleError, match="unpacks to more than"):
        fb.unpack_bundle(_build(tool, tmp_path, pem), tmp_path / "w", max_unpacked=1000, max_members=100)


def test_not_a_bundle(fb, tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00not a tarball" * 100)
    with pytest.raises(fb.BundleError, match="not a .tar"):
        fb.unpack_bundle(junk, tmp_path / "w", **LIMITS)


def test_oval_doctype_and_entities_are_refused(fb, tmp_path):
    """Billion-laughs / external-entity XML never reaches the parser's expansion."""
    x = tmp_path / "evil.oval.xml"
    x.write_text('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
                 "<oval_definitions><definitions/></oval_definitions>")
    with pytest.raises(fb.BundleError, match="DOCTYPE"):
        list(fb.iter_oval(x, {}))


def test_malformed_epss_row_names_the_line(fb, tmp_path):
    p = tmp_path / "epss.csv"
    p.write_text("cve,epss,percentile\nCVE-2024-0001,0.1,0.5\nCVE-2024-0002,abc,0.5\n")
    with pytest.raises(fb.BundleError, match=r"epss.csv:3"):
        list(fb.iter_epss(p, {}))


def test_osv_zip_of_advisories_streams(fb, tmp_path):
    """OSV publishes per-ecosystem all.zip files of one-JSON-per-advisory."""
    z = tmp_path / "Debian.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(3):
            zf.writestr(f"DEBIAN-CVE-2024-000{i}.json", json.dumps({
                "id": f"DEBIAN-CVE-2024-000{i}", "upstream": [f"CVE-2024-000{i}"],
                "affected": [{"package": {"ecosystem": "Debian:12", "name": f"p{i}"},
                              "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.0"}]}]}]}))
    stats: dict = {}
    rows = list(fb.iter_osv(z, stats))
    assert stats["entries"] == 3 and len(rows) == 3


def test_osv_open_and_multiple_ranges(fb):
    rows = fb.osv_rows({"id": "GHSA-x", "aliases": ["CVE-2024-1"], "affected": [{
        "package": {"ecosystem": "Debian:12", "name": "p"},
        "ranges": [{"type": "ECOSYSTEM", "events": [
            {"introduced": "1.0"}, {"fixed": "1.5"}, {"introduced": "2.0"}]}]}]})
    assert [(r["introduced"], r["fixed"]) for r in rows] == [("1.0", "1.5"), ("2.0", None)]


def test_cli_sample_command(tool, fb, tmp_path):
    """The dev workflow documented in the tool: `sample` makes a key + bundle
    whose printed POSTURE_FEED_TRUSTED_KEYS line verifies it."""
    assert tool.main(["sample", "--out-dir", str(tmp_path), "--version", "dev-1"]) == 0
    pub = (tmp_path / "seyalrun-dev.pub").read_text().strip()
    assert base64.b64decode(pub) and len(base64.b64decode(pub)) == 32
    keys = fb.parse_trusted_keys(f"seyalrun-dev:{pub}")
    vb = _verify(fb, tmp_path / "seyalrun-feeds-dev-1.tar.gz", keys, tmp_path)
    assert vb.version == "dev-1"
    assert oct((tmp_path / "seyalrun-dev.key").stat().st_mode)[-3:] == "600"
