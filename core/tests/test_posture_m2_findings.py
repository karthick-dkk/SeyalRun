"""posture M2 acceptance, against the exact code the service runs (findings_core,
the native plugin, risk), with an in-memory Store in place of the database:

  * the same CVE reported by two scanners is ONE finding, on the right host,
    listing both scanners;
  * an unknown host goes to the unmatched queue and is never dropped, and
    assigning it creates the findings;
  * a finding a later native scan no longer sees becomes fixed — but never
    because the feed simply has no data for that distro;
  * the risk formula and the stale-feed flag.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from libs.pluginbase import HostRef, ScanRecord, VulnScanner, discover_plugins

CORE = Path(__file__).resolve().parent.parent
SVC = CORE / "services/posture-service"
FIX = SVC / "tools/fixtures/scans"

WEB01, DB01, DB01_TWIN = "11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000002", \
    "33333333-0000-4000-8000-000000000003"
INVENTORY = [
    {"id": WEB01, "name": "web01", "ip": "10.0.0.5"},
    {"id": DB01, "name": "db01", "ip": "10.0.0.6"},
    {"id": DB01_TWIN, "name": "db01-standby", "ip": "db01-standby.example.com"},
]
T0 = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def posture(sub: str):
    if "posture_app" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "posture_app", SVC / "app/__init__.py", submodule_search_locations=[str(SVC / "app")])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["posture_app"] = mod
        spec.loader.exec_module(mod)
    return importlib.import_module(f"posture_app.{sub}")


@pytest.fixture
def core():
    return posture("findings_core")


@pytest.fixture
def plugins():
    posture("plugins.scanners")
    return discover_plugins("posture_app.plugins.scanners", VulnScanner)


class MemStore:
    """Same contract as scanning.DbStore, on dicts. Enforces the unique key."""

    def __init__(self, enrichment=None):
        self.rows: dict[str, dict] = {}
        self.events: list[dict] = []
        self.enrich = enrichment or {}

    def _check_unique(self):
        keys = [(r["host_id"], r["cve_id"], r["package"]) for r in self.rows.values()]
        assert len(keys) == len(set(keys)), "unique (host, cve, package) violated"

    async def load(self, host_id, cve_ids):
        return [dict(r) for r in self.rows.values() if r["host_id"] == host_id and r["cve_id"] in cve_ids]

    async def insert(self, rows):
        for r in rows:
            assert r["id"] not in self.rows
            self.rows[r["id"]] = dict(r)
        self._check_unique()

    async def update(self, rows):
        for r in rows:
            assert r["id"] in self.rows
            self.rows[r["id"]].update(r)
        self._check_unique()

    async def add_events(self, rows):
        self.events += rows

    async def enrichment(self, cve_ids):
        return {c: v for c, v in self.enrich.items() if c in cve_ids}

    def find(self, host_id, cve_id):
        return [r for r in self.rows.values() if r["host_id"] == host_id and r["cve_id"] == cve_id]


def run(coro):
    return asyncio.run(coro)


# web01 = Debian 12 with OpenSSL 3.0.11 installed as two binaries of one source.
WEB01_HOST = {"host_id": WEB01, "hostname": "web01", "distro_id": "debian", "distro_family": "debian",
              "distro_version": "12", "pkg_manager": "dpkg"}
WEB01_INSTALLED = [
    {"name": "libssl3", "version": "3.0.11-1~deb12u2", "source": "openssl", "source_version": "3.0.11-1~deb12u2"},
    {"name": "openssl", "version": "3.0.11-1~deb12u2", "source": "openssl", "source_version": "3.0.11-1~deb12u2"},
    {"name": "libgnutls30", "version": "3.7.9-2+deb12u1", "source": "gnutls28", "source_version": "3.7.9-2+deb12u1"},
    {"name": "zlib1g", "version": "1:1.2.13.dfsg-1", "source": "zlib", "source_version": "1:1.2.13.dfsg-1"},
]
OSV = [
    {"vuln_id": "DEBIAN-CVE-2023-5678", "cve_id": "CVE-2023-5678", "ecosystem": "Debian:12", "package": "openssl",
     "range_type": "ECOSYSTEM", "introduced": "0", "fixed": "3.0.13-1~deb12u1", "last_affected": None,
     "severity": None, "cvss": 5.3, "summary": "DH keys"},
    # same advisory for another release must not apply
    {"vuln_id": "DEBIAN-CVE-2023-5678", "cve_id": "CVE-2023-5678", "ecosystem": "Debian:11", "package": "openssl",
     "range_type": "ECOSYSTEM", "introduced": "0", "fixed": "1.1.1w-0+deb11u1", "last_affected": None,
     "severity": None, "cvss": None, "summary": None},
    # already fixed in the installed version
    {"vuln_id": "DSA-OLD", "cve_id": "CVE-2022-0001", "ecosystem": "Debian:12", "package": "openssl",
     "range_type": "ECOSYSTEM", "introduced": "0", "fixed": "3.0.9-1", "last_affected": None,
     "severity": None, "cvss": None, "summary": None},
]
OVAL = [
    {"definition_id": "oval:org.debian:def:20240553", "cve_id": "CVE-2024-0553",
     "platform": "Debian GNU/Linux 12", "package": "gnutls28", "fixed_version": "3.7.9-2+deb12u2",
     "severity": None, "cvss": None, "summary": "CVE-2024-0553 gnutls28"},
    {"definition_id": "oval:org.debian:def:x", "cve_id": "CVE-2024-0553",
     "platform": "Debian GNU/Linux 11", "package": "gnutls28", "fixed_version": "9.9",
     "severity": None, "cvss": None, "summary": None},
]


def native_records(plugins, installed=WEB01_INSTALLED, osv=OSV, oval=OVAL):
    nat = posture("plugins.scanners.native")
    pk = nat.canonical_packages(installed, "deb")
    return plugins["native"].match(WEB01_HOST, pk, osv, oval), pk


# ── correlation ──────────────────────────────────────────────────────────────

def test_host_correlation_order_and_ambiguity(core):
    idx = core.HostIndex(INVENTORY, {WEB01: "web01.prod.example.com"})
    R = HostRef
    assert idx.resolve(R(host_id=DB01, hostname="web01")) == DB01          # inventory id wins
    assert idx.resolve(R(host_id="not-a-host", hostname="web01")) == WEB01
    assert idx.resolve(R(hostname="WEB01")) == WEB01                        # case-insensitive
    assert idx.resolve(R(fqdn="web01.prod.example.com")) == WEB01           # facts hostname
    assert idx.resolve(R(hostname="web01.elsewhere.example")) == WEB01      # short name, unique
    assert idx.resolve(R(hostname="db01-standby.example.com")) == DB01_TWIN  # DNS-name address
    assert idx.resolve(R(hostname="unknown", ip="10.0.0.6")) == DB01        # finally IP
    assert idx.resolve(R(hostname="ghost.example.net", ip="10.9.9.9")) is None
    assert idx.resolve(R()) is None
    # two inventory hosts answering to one name: never guessed
    twin = core.HostIndex(INVENTORY + [{"id": "x", "name": "web01", "ip": "10.0.0.50"}])
    assert twin.resolve(R(hostname="web01")) is None
    assert twin.resolve(R(hostname="web01", ip="10.0.0.50")) is None        # ambiguous name stops the search


# ── the M2 done-when: one finding, two scanners, right host ─────────────────

def test_same_cve_from_native_and_vuls_is_one_finding_with_two_sources(core, plugins):
    store = MemStore({"CVE-2023-5678": (0.01, 0.42, False)})
    recs, pk = native_records(plugins)
    assert {(r.cve_id, r.package) for r in recs} == {("CVE-2023-5678", "openssl"), ("CVE-2024-0553", "gnutls28")}
    c, u, _ = run(core.apply_records(store, WEB01, recs, "native", T0))
    assert (c, u) == (2, 0)

    # vuls names the BINARY packages (libssl3, libgnutls30); correlation by
    # serverName -> web01, canonicalised to the source package.
    vuls = list(plugins["vuls"].parse(str(FIX / "vuls-web01.json")))
    idx = core.HostIndex(INVENTORY)
    assert {idx.resolve(r.host) for r in vuls} == {WEB01}
    b2s = posture("plugins.scanners.native").binary_to_source(WEB01_INSTALLED)
    c, u, _ = run(core.apply_records(store, WEB01, vuls, "vuls", T0 + timedelta(hours=1),
                                     canon=lambda p: b2s.get(p, p)))
    assert (c, u) == (1, 2)                       # zlib CVE is new; the two others merge

    ssl = store.find(WEB01, "CVE-2023-5678")
    assert len(ssl) == 1, ssl
    f = ssl[0]
    assert f["host_id"] == WEB01 and f["package"] == "openssl"
    assert f["sources"] == ["native", "vuls"]
    assert f["first_seen"] == T0 and f["last_seen"] == T0 + timedelta(hours=1)
    assert f["installed_version"] == "3.0.11-1~deb12u2" and f["fixed_version"] == "3.0.13-1~deb12u1"
    assert f["cvss"] == 5.3 and f["epss_percentile"] == 0.42 and f["severity"] == "medium"
    assert f["references"] and all(u.startswith("https://") for u in f["references"])
    assert len(store.find(WEB01, "CVE-2024-0553")) == 1
    assert [e["event"] for e in store.events if e["finding_id"] == f["id"]] == ["detected", "reported"]
    # and a third scanner (SARIF, binary name again) still does not split it
    sarif = list(plugins["generic"].parse(str(FIX / "trivy.sarif")))
    run(core.apply_records(store, WEB01, sarif, "generic", T0 + timedelta(hours=2), canon=lambda p: b2s.get(p, p)))
    assert [r["sources"] for r in store.find(WEB01, "CVE-2023-5678")] == [["native", "vuls", "generic"]]


def test_package_less_record_attaches_instead_of_duplicating(core, plugins):
    store = MemStore()
    recs, _ = native_records(plugins)
    run(core.apply_records(store, WEB01, recs, "native", T0))
    arf = list(plugins["openscap"].parse(str(FIX / "openscap-xccdf-arf.xml")))   # CVE-2023-5678, no package
    assert core.HostIndex(INVENTORY).resolve(arf[0].host) == WEB01
    c, u, _ = run(core.apply_records(store, WEB01, arf, "openscap", T0))
    assert (c, u) == (0, 1)
    assert [r["sources"] for r in store.find(WEB01, "CVE-2023-5678")] == [["native", "openscap"]]

    # the other order: package-less first, then a record naming the package fills it in
    store2 = MemStore()
    run(core.apply_records(store2, WEB01, arf, "openscap", T0))
    run(core.apply_records(store2, WEB01, recs, "native", T0))
    rows = store2.find(WEB01, "CVE-2023-5678")
    assert len(rows) == 1 and rows[0]["package"] == "openssl" and rows[0]["sources"] == ["openscap", "native"]


def test_duplicate_records_in_one_file_are_one_finding(core):
    store = MemStore()
    r = ScanRecord(host=HostRef(hostname="web01"), cve_id="CVE-2024-1111", package="curl", severity="low")
    r2 = ScanRecord(host=HostRef(hostname="web01"), cve_id="CVE-2024-1111", package="curl", severity="critical",
                    cvss=9.1, references=["javascript:alert(1)", "https://example.org/x"])
    c, u, _ = run(core.apply_records(store, WEB01, [r, r2], "generic", T0))
    assert (c, u) == (1, 0)
    f = store.find(WEB01, "CVE-2024-1111")[0]
    assert f["severity"] == "critical" and f["cvss"] == 9.1 and f["references"] == ["https://example.org/x"]


# ── unmatched: never dropped, assignable ────────────────────────────────────

def test_unknown_host_goes_to_unmatched_and_assignment_creates_findings(core, plugins):
    recs = list(plugins["generic"].parse(str(FIX / "generic-mixed.csv")))
    idx = core.HostIndex(INVENTORY)
    matched, orphans = [], []
    for r in recs:
        hid = idx.resolve(r.host)
        (matched if hid else orphans).append((hid, r))
    assert [h for h, _ in matched] == [WEB01, DB01]
    groups = core.group_unmatched((r.host, r) for _, r in orphans)
    assert len(groups) == 1
    g = next(iter(groups.values()))
    assert core.reported_host_label(g["ref"]) == "ghost.example.net" and g["ref"].ip == "10.9.9.9"
    assert sorted(r.cve_id for r in g["records"]) == ["CVE-2021-44228", "CVE-2022-22965"]
    assert len(matched) + sum(len(x["records"]) for x in groups.values()) == len(recs)   # nothing dropped

    # an admin assigns the queued result to db01: the findings appear there
    store = MemStore()
    c, _, _ = run(core.apply_records(store, DB01, g["records"], "generic", T0, actor="admin"))
    assert c == 2 and len(store.find(DB01, "CVE-2021-44228")) == 1
    assert store.events[0]["by"] == "admin"


def test_file_with_no_host_at_all_is_still_queued(core):
    rec = ScanRecord(host=HostRef(), cve_id="CVE-2024-3333")
    idx = core.HostIndex(INVENTORY)
    assert idx.resolve(rec.host) is None
    groups = core.group_unmatched([(rec.host, rec)])
    assert core.reported_host_label(next(iter(groups.values()))["ref"]) == "(no host in file)"


def test_import_path_queues_every_unresolved_record():
    """Source-shape: the import loop sends every record either to a host or to
    the unmatched queue — there is no third branch that drops one."""
    src = (SVC / "app/scanning.py").read_text()
    loop = src.split("async def apply_import", 1)[1].split("async def assign_unmatched", 1)[0]
    assert "hid = override_host or index.resolve(rec.host)" in loop
    assert "per_host.setdefault(hid, []).append(rec)" in loop
    assert "orphans.setdefault(rec.host.key(), (rec.host, []))[1].append(rec)" in loop
    assert "_queue_unmatched(" in loop
    assign = src.split("async def assign_unmatched", 1)[1].split("# ── scans table", 1)[0]
    assert 'u.status = "assigned"' in assign and "delete(" not in assign


# ── lifecycle: fixed, reopened, accepted ─────────────────────────────────────

def test_native_rescan_marks_fixed_and_reopens(core, plugins):
    store = MemStore()
    recs, pk = native_records(plugins)
    run(core.apply_records(store, WEB01, recs, "native", T0))
    # vuls-only finding on zlib (still installed, no fix) and on a removed package
    run(core.apply_records(store, WEB01, [
        ScanRecord(host=HostRef(), cve_id="CVE-2024-9999", package="zlib", installed_version="1:1.2.13.dfsg-1"),
        ScanRecord(host=HostRef(), cve_id="CVE-2020-0002", package="telnetd", fixed_version="1.0"),
        ScanRecord(host=HostRef(), cve_id="CVE-2020-0003", package="gnutls28", fixed_version="3.7.9-2+deb12u1"),
    ], "vuls", T0))

    # openssl upgraded to the fixed version; feed unchanged
    upgraded = [dict(p, version="3.0.13-1~deb12u1", source_version="3.0.13-1~deb12u1")
                if p["source"] == "openssl" else p for p in WEB01_INSTALLED]
    recs2, pk2 = native_records(plugins, installed=upgraded)
    _, _, seen = run(core.apply_records(store, WEB01, recs2, "native", T0 + timedelta(days=1)))
    current = [r for r in store.rows.values() if r["host_id"] == WEB01]
    fixed = dict((r["cve_id"], why) for r, why in core.fixed_after_native(current, seen, pk2, "deb", True))
    assert set(fixed) == {"CVE-2023-5678", "CVE-2020-0002", "CVE-2020-0003"}
    assert "no longer detected" in fixed["CVE-2023-5678"]
    assert "no longer installed" in fixed["CVE-2020-0002"]
    assert ">= fixed" in fixed["CVE-2020-0003"]
    assert "CVE-2024-9999" not in fixed and "CVE-2024-0553" not in fixed

    # apply the fix, then a scanner sees it again -> reopened
    for r, _ in core.fixed_after_native(current, seen, pk2, "deb", True):
        store.rows[r["id"]]["status"] = core.FIXED
    run(core.apply_records(store, WEB01, recs, "native", T0 + timedelta(days=2)))
    f = store.find(WEB01, "CVE-2023-5678")[0]
    assert f["status"] == "open" and any(e["event"] == "reopened" for e in store.events)


def test_feed_without_data_for_the_distro_marks_nothing_fixed(core, plugins):
    store = MemStore()
    recs, pk = native_records(plugins)
    run(core.apply_records(store, WEB01, recs, "native", T0))
    # a new feed with no Debian 12 advisories at all: native sees nothing
    nat = posture("plugins.scanners.native")
    assert not nat.NativeScanner.covered([], [], WEB01_HOST)
    current = list(store.rows.values())
    assert core.fixed_after_native(current, set(), pk, "deb", native_trusted=False) == []
    # and no facts at all proves nothing either
    assert core.fixed_after_native(current, set(), {}, "deb", native_trusted=True) == []


def test_accepted_stays_accepted_while_reported(core, plugins):
    store = MemStore()
    recs, _ = native_records(plugins)
    run(core.apply_records(store, WEB01, recs, "native", T0))
    f = store.find(WEB01, "CVE-2024-0553")[0]
    store.rows[f["id"]]["status"] = core.ACCEPTED
    run(core.apply_records(store, WEB01, recs, "native", T0 + timedelta(days=1)))
    assert store.find(WEB01, "CVE-2024-0553")[0]["status"] == "accepted"


# ── native matching details ──────────────────────────────────────────────────

def test_native_matches_rpm_hosts_on_binary_names_with_rpm_order(plugins):
    nat = posture("plugins.scanners.native")
    host = {"host_id": DB01, "hostname": "db01", "distro_id": "rocky", "distro_family": "rhel",
            "distro_version": "9.3", "pkg_manager": "rpm"}
    installed = [{"name": "openssl-libs", "version": "1:3.0.7-24.el9"},
                 {"name": "glibc", "version": "2.34-100.el9_4.2"}]
    osv = [
        {"vuln_id": "RLSA-2024:0627", "cve_id": "CVE-2023-5678", "ecosystem": "Rocky Linux:9", "package": "openssl-libs",
         "range_type": "ECOSYSTEM", "introduced": "0", "fixed": "1:3.0.7-25.el9_3", "last_affected": None,
         "severity": "Moderate", "cvss": None, "summary": None},
        {"vuln_id": "RLSA-2024:2722", "cve_id": "CVE-2024-2961", "ecosystem": "Rocky Linux:9", "package": "glibc",
         "range_type": "ECOSYSTEM", "introduced": "0", "fixed": "2.34-100.el9_4.2", "last_affected": None,
         "severity": "Important", "cvss": None, "summary": None},
        {"vuln_id": "ALSA-1", "cve_id": "CVE-2024-7777", "ecosystem": "AlmaLinux:9", "package": "glibc",
         "range_type": "ECOSYSTEM", "introduced": "0", "fixed": "9.9", "last_affected": None,
         "severity": None, "cvss": None, "summary": None},
    ]
    oval = [{"definition_id": "oval:rh:1", "cve_id": "CVE-2023-5678", "platform": "Red Hat Enterprise Linux 9",
             "package": "openssl-libs", "fixed_version": "1:3.0.7-27.el9", "severity": "Important", "cvss": 7.0,
             "summary": "RHSA"}]
    pk = nat.canonical_packages(installed, "rpm")
    recs = plugins["native"].match(host, pk, osv, oval)
    assert [(r.cve_id, r.package) for r in recs] == [("CVE-2023-5678", "openssl-libs")]
    r = recs[0]
    assert r.fixed_version == "1:3.0.7-27.el9"                 # the fix that satisfies OSV and OVAL
    assert r.severity == "high" and r.cvss == 7.0
    assert nat.osv_like_patterns("rocky", "rhel", "9.3") == ["Rocky Linux:9%"]


def test_native_ecosystem_and_platform_matching():
    nat = posture("plugins.scanners.native")
    assert nat.ecosystem_matches("Ubuntu:22.04:LTS", "ubuntu", "debian", "22.04")
    assert not nat.ecosystem_matches("Ubuntu:Pro:22.04:LTS", "ubuntu", "debian", "22.04")
    assert not nat.ecosystem_matches("Ubuntu:20.04:LTS", "ubuntu", "debian", "22.04")
    assert nat.ecosystem_matches("Red Hat:enterprise_linux:9::appstream", "rhel", "rhel", "9.4")
    assert not nat.ecosystem_matches("Red Hat:enterprise_linux:8::appstream", "rhel", "rhel", "9.4")
    assert not nat.ecosystem_matches("Debian:12", "rocky", "rhel", "9")
    assert nat.oval_platform_matches("Red Hat Enterprise Linux 8; Red Hat Enterprise Linux 9", "almalinux", "rhel", "9.2")
    assert not nat.oval_platform_matches("Red Hat Enterprise Linux 10", "rhel", "rhel", "1")
    assert not nat.oval_platform_matches("", "debian", "debian", "12")


# ── risk + staleness ─────────────────────────────────────────────────────────

def test_risk_formula():
    risk = posture("risk")
    rs = risk.risk_score
    assert rs(9.8, "critical", 0.05, False) == 61          # severe, unlikely
    assert rs(7.5, "high", 0.99, False) == 85               # likely
    assert rs(2.0, "low", 0.01, True) == 90                 # KEV floor
    assert rs(9.8, "critical", 0.99, True) == 98            # KEV never lowers a score
    assert rs(10.0, "critical", 1.0, False) == 100 and rs(0.0, "unknown", 0.0, False) == 0
    assert rs(None, "high", None, False) == round(100 * (0.6 * 0.75 + 0.4 * 0.5))   # missing CVSS + EPSS
    assert rs(None, "unknown", None, False) == 50           # unknown is not harmless
    assert rs(None, "garbage", 2.0, False) == round(100 * (0.6 * 0.5 + 0.4))        # p clamped
    for args in [(c, s, p, k) for c in (None, 0.0, 5.0, 10.0) for s in risk.SEVERITIES
                 for p in (None, 0.0, 0.5, 1.0) for k in (False, True)]:
        assert 0 <= rs(*args) <= 100
        assert rs(*args[:3], True) >= 90


def test_severity_normalisation():
    risk = posture("risk")
    n = risk.normalise_severity
    assert [n(s) for s in ("Important", "Moderate", "unimportant", "CRITICAL", "error", "note", "")] == \
        ["high", "medium", "low", "critical", "high", "low", "unknown"]
    assert n("", 9.1) == "critical" and n(None, 4.0) == "medium" and n("not yet assigned", None) == "unknown"
    assert risk.worse("low", "high") == "high" and risk.worse("unknown", "low") == "low"


def test_stale_feed_flag():
    risk = posture("risk")
    now = datetime(2026, 11, 23, tzinfo=UTC)
    assert risk.feed_age_days(now - timedelta(days=3, hours=5), now) == 3
    assert risk.feed_age_days(None) is None
    assert risk.feed_age_days(datetime(2026, 11, 13), now) == 10          # naive = UTC
    assert risk.feed_stale(7, 7) is False and risk.feed_stale(8, 7) is True
    assert risk.feed_stale(None, 7) is True                               # no active feed
    cfg = (SVC / "app/config.py").read_text()
    assert "posture_feed_stale_days: int = 7" in cfg
