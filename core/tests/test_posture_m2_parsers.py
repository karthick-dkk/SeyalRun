"""posture M2: every scanner plugin parses its format into normalised records —
on small fixtures shaped like the real tools' output — refuses bad input with a
400-able message, and never lets a non-web reference URL through.

The plugins are imported as a real package (posture-service's ``app`` loaded
under the alias ``posture_app``) because they use package-relative imports, the
same way discover_plugins imports them in the service.
"""

from __future__ import annotations

import gzip
import importlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from libs.pluginbase import ScanParseError, VulnScanner, discover_plugins

CORE = Path(__file__).resolve().parent.parent
SVC = CORE / "services/posture-service"
FIX = SVC / "tools/fixtures/scans"


def posture(sub: str):
    if "posture_app" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "posture_app", SVC / "app/__init__.py", submodule_search_locations=[str(SVC / "app")])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["posture_app"] = mod
        spec.loader.exec_module(mod)
    return importlib.import_module(f"posture_app.{sub}")


def scanner(name: str):
    posture("plugins.scanners")
    return discover_plugins("posture_app.plugins.scanners", VulnScanner)[name]


def records(name: str, path: Path):
    return list(scanner(name).parse(str(path)))


def _refs_are_web_only(recs):
    for r in recs:
        for u in r.references:
            assert u.split(":", 1)[0] in ("http", "https"), u


# ── discovery ────────────────────────────────────────────────────────────────

def test_four_scanners_are_discovered_as_plugins():
    posture("plugins.scanners")
    reg = discover_plugins("posture_app.plugins.scanners", VulnScanner)
    assert set(reg) == {"native", "generic", "openscap", "vuls"}
    assert reg["native"].kind == "builtin" and reg["native"].accepts_upload is False
    for n in ("generic", "openscap", "vuls"):
        assert reg[n].kind == "plugin" and reg[n].accepts_upload and reg[n].formats
    assert set(reg["generic"].formats) == {"csv", "cyclonedx-vex", "sarif"}


def test_gpl_tools_are_never_imported():
    for f in (SVC / "app").rglob("*.py"):
        src = f.read_text()
        for mod in ("openscap", "oscap", "vuls"):
            assert f"import {mod}" not in src and f"from {mod} " not in src, f
        assert "subprocess" not in src, f"{f}: posture never executes a scanner"


# ── vuls ─────────────────────────────────────────────────────────────────────

def test_vuls_report():
    recs = records("vuls", FIX / "vuls-web01.json")
    by = {(r.cve_id, r.package): r for r in recs}
    assert set(by) == {("CVE-2023-5678", "libssl3"), ("CVE-2024-0553", "libgnutls30"),
                       ("CVE-2024-9999", "zlib1g")}
    ssl = by[("CVE-2023-5678", "libssl3")]
    assert ssl.host.hostname == "web01" and ssl.host.ip == "10.0.0.5"   # loopback skipped
    assert ssl.installed_version == "3.0.11-1~deb12u2" and ssl.fixed_version == "3.0.13-1~deb12u1"
    assert ssl.cvss == 5.3 and ssl.severity == "MEDIUM" and "DH keys" in ssl.summary
    # the javascript: and data: references in the file are gone
    assert ssl.references == ["https://nvd.nist.gov/vuln/detail/CVE-2023-5678",
                              "https://www.openssl.org/news/secadv/20231106.txt"]
    gnutls = by[("CVE-2024-0553", "libgnutls30")]          # old dict-shaped cveContents
    assert gnutls.cvss == 7.5 and gnutls.installed_version == "3.7.9-2+deb12u1"
    z = by[("CVE-2024-9999", "zlib1g")]
    assert z.fixed_version is None and z.installed_version == "1:1.2.13.dfsg-1"
    _refs_are_web_only(recs)


def test_vuls_streams_a_report_bigger_than_one_read(tmp_path):
    cves = {f"CVE-2024-{10000 + i}": {"cveID": f"CVE-2024-{10000 + i}",
                                       "affectedPackages": [{"name": "pkg%d" % i, "fixedIn": "1.%d" % i}],
                                       "cveContents": {"nvd": [{"summary": "x" * 500, "cvss3Score": 5.0}]}}
            for i in range(3000)}
    p = tmp_path / "big.json.gz"
    p.write_bytes(gzip.compress(json.dumps({"serverName": "web01", "scannedCves": cves,
                                            "packages": {}}).encode()))
    recs = records("vuls", p)
    assert len(recs) == 3000 and recs[-1].fixed_version == "1.2999"


@pytest.mark.parametrize("body,msg", [
    (b"not json at all", "not a Vuls JSON report"),
    (b'{"serverName": "x"}', "no 'scannedCves'"),
    (b'{"serverName": "x", "scannedCves": {"CVE-2024-1": {', "invalid Vuls report"),
])
def test_vuls_rejects_bad_input(tmp_path, body, msg):
    p = tmp_path / "bad.json"
    p.write_bytes(body)
    with pytest.raises(ScanParseError, match=msg):
        records("vuls", p)


# ── generic: CSV, CycloneDX VEX, SARIF ───────────────────────────────────────

def test_generic_csv():
    recs = records("generic", FIX / "generic-mixed.csv")
    assert len(recs) == 4
    first = recs[0]
    assert first.host.hostname == "web01.example.com" and first.cve_id == "CVE-2023-5678"
    assert first.package == "libssl3" and first.cvss == 5.3 and first.severity == "Moderate"
    assert first.references == ["https://example.org/a"]          # javascript:alert(1) dropped
    assert recs[1].host.ip == "10.0.0.6" and not recs[1].host.hostname
    _refs_are_web_only(recs)


def test_generic_csv_errors(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("host,package\nweb01,openssl\n")
    with pytest.raises(ScanParseError, match="needs a 'cve' column"):
        records("generic", p)
    p.write_text("host,cve\nweb01,not-a-cve\n")
    with pytest.raises(ScanParseError, match="line 2"):
        records("generic", p)
    p.write_text("host,cve,cvss\nweb01,CVE-2024-1234,eleven\n")
    with pytest.raises(ScanParseError, match="cvss"):
        records("generic", p)


def test_generic_cyclonedx_vex():
    recs = records("generic", FIX / "cyclonedx-vex.json")
    by = {r.cve_id: r for r in recs}
    assert set(by) == {"CVE-2024-2961", "CVE-2023-0001"}          # not_affected is not a finding
    g = by["CVE-2024-2961"]
    assert g.host.hostname == "db01" and g.host.ip == "10.0.0.6"
    assert (g.package, g.installed_version) == ("glibc", "2.34-100.el9")   # from the purl
    assert g.cvss == 8.8 and g.severity == "high"
    assert g.references == ["https://nvd.nist.gov/vuln/detail/CVE-2024-2961",
                            "https://errata.rockylinux.org/RLSA-2024:2722"]   # vbscript: dropped
    o = by["CVE-2023-0001"]                                        # CVE from a GHSA's references
    assert (o.package, o.installed_version) == ("openssl-libs", "1:3.0.7-24.el9")  # components after vulns
    _refs_are_web_only(recs)


def test_generic_sarif_trivy():
    recs = records("generic", FIX / "trivy.sarif")
    assert len(recs) == 1                                          # the SAST result has no CVE
    r = recs[0]
    assert r.host.hostname == "web01" and r.cve_id == "CVE-2023-5678"
    assert (r.package, r.installed_version, r.fixed_version) == ("libssl3", "3.0.11-1~deb12u2", "3.0.13-1~deb12u1")
    assert r.cvss == 5.3 and r.severity == "MEDIUM"
    assert r.summary == "openssl: excessive time spent in DH check"
    assert r.references == ["https://avd.aquasec.com/nvd/cve-2023-5678"]   # rule helpUri javascript: dropped


def test_generic_rejects_unknown_json(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"hello": "world"}')
    with pytest.raises(ScanParseError, match="neither CycloneDX"):
        records("generic", p)


# ── openscap ─────────────────────────────────────────────────────────────────

def test_openscap_oval_results():
    recs = records("openscap", FIX / "openscap-oval-results.xml")
    assert len(recs) == 1                                          # the result="false" definition is not a finding
    r = recs[0]
    assert r.cve_id == "CVE-2024-2961" and r.package == "glibc" and r.fixed_version == "0:2.34-100.el9_4.2"
    assert r.host.hostname == "db01.example.com" and r.host.ip == "10.0.0.6"
    assert r.cvss == 8.8 and r.severity == "Important" and r.summary.startswith("RHSA-2024:2722")
    assert "javascript:alert(1)" not in r.references
    assert "https://access.redhat.com/errata/RHSA-2024:2722" in r.references
    _refs_are_web_only(recs)


def test_openscap_arf_xccdf():
    recs = records("openscap", FIX / "openscap-xccdf-arf.xml")
    assert [(r.cve_id, r.package) for r in recs] == [("CVE-2023-5678", "")]   # the "pass" rule is not a finding
    h = recs[0].host
    assert (h.hostname, h.fqdn, h.ip) == ("web01", "web01.example.com", "10.0.0.5")


def test_openscap_refuses_doctype_and_garbage(tmp_path):
    p = tmp_path / "evil.xml"
    p.write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><oval_results>&a;</oval_results>')
    with pytest.raises(ScanParseError, match="DOCTYPE"):
        records("openscap", p)
    p.write_text("<oval_results><unclosed></oval_results>")
    with pytest.raises(ScanParseError, match="malformed XML"):
        records("openscap", p)
    p.write_text("<html><body>hello</body></html>")
    with pytest.raises(ScanParseError, match="not an OpenSCAP result"):
        records("openscap", p)


# ── helpers ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,ok", [
    ("https://nvd.nist.gov/x", True),
    ("http://example.org/a?b=c", True),
    ("HTTPS://EXAMPLE.ORG", True),
    ("javascript:alert(1)", False),
    ("JavaScript:alert(1)", False),
    (" javascript:alert(1)", False),
    ("java\tscript:alert(1)", False),
    ("data:text/html,<script>", False),
    ("vbscript:msgbox", False),
    ("file:///etc/passwd", False),
    ("//evil.example/x", False),
    ("https://", False),
    ("/relative/path", False),
    (None, False),
])
def test_safe_url(url, ok):
    refs = posture("refs")
    assert (refs.safe_url(url) is not None) is ok


def test_jsonstream_handles_numbers_split_across_reads():
    js = posture("jsonstream")
    lz = js.Lazy(io.StringIO('{"a": 1234567, "b": [1, 2, {"c": "d"}], "e": null}'), chunk=3)
    got = {}
    for k in lz.members():
        got[k] = lz.value()
    assert got == {"a": 1234567, "b": [1, 2, {"c": "d"}], "e": None}


def test_jsonstream_bounds_a_single_value():
    js = posture("jsonstream")
    lz = js.Lazy(io.StringIO('{"a": "' + "x" * 5000 + '"}'), max_value=1000, chunk=64)
    with pytest.raises(js.JsonStreamError, match="exceeds"):
        for _ in lz.members():
            lz.value()


def test_feed_osv_rows_carry_cvss_and_summary():
    fb = posture("feedbundle")
    assert fb.cvss3_base("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8
    assert fb.cvss3_base("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H") == 10.0
    assert fb.cvss3_base("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:L") == 5.3
    assert fb.cvss3_base("CVSS:3.0/AV:L/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:N") == 0.0
    assert fb.cvss3_base("garbage") is None
    rows = fb.osv_rows({"id": "X-1", "aliases": ["CVE-2024-1"], "summary": "short text",
                        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
                        "affected": [{"package": {"ecosystem": "Debian:12", "name": "p"},
                                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1"}]}]}]})
    assert rows[0]["cvss"] == 9.8 and rows[0]["summary"] == "short text"


def test_feed_oval_rows_carry_cvss_and_title(tmp_path):
    fb = posture("feedbundle")
    rows = list(fb.iter_oval(SVC / "tools/fixtures/rhel-debian-sample.oval.xml", {}))
    rh = next(r for r in rows if r["package"] == "openssl")
    assert rh["cvss"] == 5.3 and rh["summary"].startswith("RHSA-2024:0627")
