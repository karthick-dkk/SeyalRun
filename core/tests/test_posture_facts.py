"""posture facts: the collector script's output, as automation-service stores it,
parses into the facts the Posture UI shows — for a Debian-family and a
RHEL-family host.

Two layers. The fixtures below are record streams shaped exactly like
posture_facts.sh emits on Debian 12 and Rocky 9, wrapped the way the job output
actually arrives (gzip+base64 between markers, every line "[host:<id>] "
prefixed, progress chatter around it). Then the REAL script is run on whatever
machine runs this suite and round-tripped through the parser — on the CI runner
(Ubuntu) and in python:3.12-slim (Debian) that is a genuine dpkg host, so the
script and parser are checked against each other, not just against my fixture.
"""

from __future__ import annotations

import base64
import gzip
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FACTS = "services/posture-service/app/facts.py"
SCRIPT = "services/automation-service/app/plugins/executors/posture_facts.sh"
HOST = "6f1c1f0e-0000-4000-8000-000000000001"


def _job_output(records: str, encoding: str = "gz", width: int = 76) -> list[str]:
    data = records.encode()
    if encoding == "gz":
        data = gzip.compress(data)
    b64 = base64.b64encode(data).decode()
    body = [b64[i:i + width] for i in range(0, len(b64), width)]
    lines = [f"[host:{HOST}] fetching host info...", "[host:10.0.0.5] connecting...",
             f"SEYALRUN-FACTS-BEGIN v1 {encoding}", *body, "SEYALRUN-FACTS-END"]
    return [ln if ln.startswith("[host:") else f"[host:{HOST}] {ln}" for ln in lines]


DEBIAN = "\n".join([
    "V\t1", "H\tweb-deb12", "K\t6.1.0-18-amd64", "A\tx86_64",
    'R\tPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"', 'R\tNAME="Debian GNU/Linux"',
    'R\tVERSION_ID="12"', "R\tID=debian",
    "M\tdpkg",
    "I\topenssl\t3.0.11-1~deb12u1\tamd64\tinstall ok installed",
    "I\tlibssl3\t3.0.11-1~deb12u1\tamd64\tinstall ok installed",
    "I\tbash\t5.2.15-2+b2\tamd64\tinstall ok installed",
    "I\tlinux-image-6.1.0-18-amd64\t6.1.76-1\tamd64\tinstall ok installed",
    "I\tlinux-image-6.1.0-21-amd64\t6.1.90-1\tamd64\tinstall ok installed",
    "I\toldpkg\t1.0\tamd64\tdeinstall ok config-files",
    "P\topenssl/stable-security 3.0.13-1~deb12u1 amd64 [upgradable from: 3.0.11-1~deb12u1]",
    "P\tlibssl3/stable-security,stable 3.0.13-1~deb12u1 amd64 [upgradable from: 3.0.11-1~deb12u1]",
    "P\ttzdata/stable-updates 2024a-0+deb12u1 all [upgradable from: 2023c-5]",
    "Z\tend", "",
])

ROCKY = "\n".join([
    "V\t1", "H\tdb-rocky9", "K\t5.14.0-362.8.1.el9_3.x86_64", "A\tx86_64",
    'R\tNAME="Rocky Linux"', 'R\tVERSION_ID="9.3"', 'R\tID="rocky"', 'R\tID_LIKE="rhel centos fedora"',
    'R\tPRETTY_NAME="Rocky Linux 9.3 (Blue Onyx)"', "L\tRocky Linux release 9.3 (Blue Onyx)",
    "M\trpm",
    "I\topenssl\t1:3.0.7-24.el9\tx86_64\tinstalled",
    "I\topenssl-libs\t1:3.0.7-24.el9\tx86_64\tinstalled",
    "I\tkernel-core\t5.14.0-362.8.1.el9_3\tx86_64\tinstalled",
    "I\tgpg-pubkey\t350d275d-6279464b\t(none)\tinstalled",
    "I\tpython3-a-very-long-package-name-that-wraps\t3.9.18-1.el9\tnoarch\tinstalled",
    "N\tkernel-core\t5.14.0-362.8.1.el9_3.x86_64\t1700000000",
    "N\tkernel-core\t5.14.0-362.13.1.el9_3.x86_64\t1702000000",
    "C\topenssl.x86_64                1:3.0.7-25.el9_3          baseos",
    "C\topenssl-libs.x86_64           1:3.0.7-25.el9_3          baseos",
    "C\tpython3-a-very-long-package-name-that-wraps.noarch",
    "C\t                              3.9.18-3.el9_3            appstream",
    "C\tObsoleting Packages",
    "C\tgrub2-tools.x86_64            1:2.06-70.el9             baseos",
    "Q\tRLSA-2024:0627 Moderate/Sec.  openssl-1:3.0.7-25.el9_3.x86_64",
    "Q\tRLSA-2024:0627 Moderate/Sec.  openssl-libs-1:3.0.7-25.el9_3.x86_64",
    "Z\tend", "",
])


@pytest.fixture
def facts(load_module):
    return load_module(FACTS, "posture_facts")


def test_debian_family_host(facts):
    f = facts.parse_facts(facts.extract_payload(_job_output(DEBIAN)))
    assert (f["distro_family"], f["distro_id"], f["distro_version"]) == ("debian", "debian", "12")
    assert f["distro"] == "Debian GNU/Linux" and f["kernel"] == "6.1.0-18-amd64"
    assert f["hostname"] == "web-deb12" and f["pkg_manager"] == "dpkg"
    names = {p["name"] for p in f["installed"]}
    assert "oldpkg" not in names, "a removed package with only config files left is not installed"
    assert len(f["installed"]) == 5
    pend = {p["name"]: p for p in f["pending"]}
    assert set(pend) == {"openssl", "libssl3", "tzdata"}
    assert pend["openssl"]["security"] and pend["libssl3"]["security"] and not pend["tzdata"]["security"]
    assert pend["openssl"]["current"] == "3.0.11-1~deb12u1" and pend["openssl"]["available"] == "3.0.13-1~deb12u1"
    assert f["security_updates"] == 2
    # no reboot-required flag source -> newer installed linux-image than the running kernel
    assert f["reboot_required"] is True and f["reboot_method"] == "kernel-compare"


def test_rhel_family_host(facts):
    f = facts.parse_facts(facts.extract_payload(_job_output(ROCKY, width=4000)))
    assert (f["distro_family"], f["distro_id"], f["distro_version"]) == ("rhel", "rocky", "9.3")
    assert f["pkg_manager"] == "rpm"
    assert "gpg-pubkey" not in {p["name"] for p in f["installed"]}
    pend = {p["name"]: p for p in f["pending"]}
    # the wrapped yum line is joined; anything after "Obsoleting Packages" is not an update
    assert set(pend) == {"openssl", "openssl-libs", "python3-a-very-long-package-name-that-wraps"}
    assert pend["openssl"]["current"] == "1:3.0.7-24.el9" and pend["openssl"]["available"] == "1:3.0.7-25.el9_3"
    assert pend["openssl"]["security"] and pend["openssl-libs"]["security"]
    assert not pend["python3-a-very-long-package-name-that-wraps"]["security"]
    assert f["security_updates"] == 2
    assert f["reboot_required"] is True, "newest installed kernel is not the running one"


def test_explicit_reboot_flag_wins(facts):
    text = DEBIAN.replace("Z\tend", "B\t0\treboot-required-file\nZ\tend")
    f = facts.parse_facts(facts.extract_payload(_job_output(text)))
    assert f["reboot_required"] is False and f["reboot_method"] == "reboot-required-file"


def test_raw_encoding_is_accepted(facts):
    f = facts.parse_facts(facts.extract_payload(_job_output(ROCKY, encoding="raw")))
    assert f["distro_id"] == "rocky"


@pytest.mark.parametrize("mangle, why", [
    (lambda ls: ls[:-1], "END marker missing (truncated output)"),
    (lambda ls: [x for x in ls if "SEYALRUN-FACTS-BEGIN" not in x], "BEGIN marker missing"),
    (lambda ls: ls[:3] + [ls[3][:-10] + "****" + ls[3][-6:]] + ls[4:], "a masked secret inside the payload"),
])
def test_altered_output_is_an_error_not_wrong_facts(facts, mangle, why):
    with pytest.raises(facts.FactsError):
        facts.parse_facts(facts.extract_payload(mangle(_job_output(DEBIAN))))


def test_payload_without_end_sentinel_is_refused(facts):
    with pytest.raises(facts.FactsError, match="incomplete"):
        facts.extract_payload(_job_output(DEBIAN.replace("Z\tend", "")))


def test_failure_reason_comes_from_job_output(facts):
    lines = [f"[host:{HOST}] fetching host info...", f"[host:{HOST}] no credential available to connect"]
    assert facts.last_error_line(lines) == "no credential available to connect"


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("base64"), reason="needs bash + base64")
def test_real_script_round_trips_through_the_parser(facts):
    """Run the shipped script here and parse it. On a dpkg/rpm machine (CI's
    Ubuntu runner, python:3.12-slim) this is a real package inventory."""
    proc = subprocess.run(["bash", str(ROOT / SCRIPT)], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    lines = [f"[host:{HOST}] {ln}" for ln in proc.stdout.splitlines()]
    f = facts.parse_facts(facts.extract_payload(lines))
    assert f["kernel"] and f["arch"]
    if shutil.which("dpkg-query") or shutil.which("rpm"):
        assert f["pkg_manager"] in ("dpkg", "rpm")
        assert len(f["installed"]) > 10, f"only {len(f['installed'])} packages parsed"
        assert all(p["name"] and p["version"] for p in f["installed"])
    else:
        assert any("unsupported package manager" in w for w in f["warnings"])


def test_failed_update_listing_is_unknown_not_up_to_date(facts):
    """A host whose check-update failed has an empty update list. That must read as
    "unknown", or the rollout planner reports it as fully patched."""
    text = "\n".join(ln for ln in ROCKY.splitlines() if not ln.startswith(("C\t", "Q\t")))
    text = text.replace("Z\tend", "E\tdnf check-update failed (exit 1): pending updates unknown\nZ\tend")
    f = facts.parse_facts(facts.extract_payload(_job_output(text)))
    assert f["pending"] == [] and f["pending_known"] is False
    assert facts.parse_facts(facts.extract_payload(_job_output(ROCKY)))["pending_known"] is True
    # A warning that is not about the update list leaves it known.
    assert facts.updates_known(["zypper does not say which package updates are security updates"])
    assert not facts.updates_known(["x", "no apt front-end found: pending updates unknown"])
    assert facts.updates_known(None) and facts.updates_known([])


_FAKE_RPM = """#!/bin/bash
case "$*" in
  *kernel-core*) printf 'N\\tkernel-core\\t0.0.1-fake.el9.x86_64\\t1700000000\\n' ;;
esac
exit 0
"""
_FAKE_DNF = """#!/bin/bash
case "$*" in
  *"needs-restarting --help"*) [ -n "$NR_PLUGIN" ] && exit 0; echo "No such command: needs-restarting" >&2; exit 1 ;;
  *"needs-restarting -r"*) [ -n "$NR_PLUGIN" ] && exit "$NR_RC"; echo "No such command: needs-restarting" >&2; exit 1 ;;
  *check-update*) exit "${CU_RC:-0}" ;;
esac
exit 0
"""


def _fake_el9(tmp_path, **env):
    """The real script on a fake minimal EL9: rpm and dnf are stubs, and no
    needs-restarting command is on PATH."""
    import os
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "gzip", "base64", "grep", "cat", "head", "uname", "hostname", "sed", "tr"):
        real = shutil.which(tool)
        if real:
            (bindir / tool).symlink_to(real)
    for name, body in (("rpm", _FAKE_RPM), ("dnf", _FAKE_DNF)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    # The script pins its own PATH; this copy differs only in that line, so the stubs are found.
    real = (ROOT / SCRIPT).read_text()
    pin = "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
    assert real.count(pin) == 1
    script = tmp_path / "posture_facts.sh"
    script.write_text(real.replace(pin, f"PATH={bindir}\n"))
    proc = subprocess.run([str(bindir / "bash"), str(script)], capture_output=True, text=True, timeout=120,
                          env={"PATH": str(bindir), **env}, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    return [f"[host:{HOST}] {ln}" for ln in proc.stdout.splitlines()]


@pytest.mark.skipif(not all(shutil.which(t) for t in ("bash", "gzip", "base64", "grep", "uname")), reason="needs bash tools")
@pytest.mark.parametrize("plugin,rc,reboot,method", [
    ("1", "0", False, "needs-restarting"),    # glibc/systemd not updated: the plugin says no reboot
    ("1", "1", True, "needs-restarting"),     # the plugin says reboot, whatever the kernel says
    ("", "1", True, "kernel-compare"),        # no plugin: "no such command" (exit 1) is not "reboot"
])
def test_minimal_el9_uses_the_dnf_needs_restarting_plugin(facts, tmp_path, plugin, rc, reboot, method):
    f = facts.parse_facts(facts.extract_payload(_fake_el9(tmp_path, NR_PLUGIN=plugin, NR_RC=rc)))
    assert f["pkg_manager"] == "rpm"
    assert (f["reboot_required"], f["reboot_method"]) == (reboot, method)
    assert not any("needs-restarting" in w for w in f["warnings"])


@pytest.mark.skipif(not all(shutil.which(t) for t in ("bash", "gzip", "base64", "grep", "uname")), reason="needs bash tools")
def test_real_script_marks_a_failed_check_update_unknown(facts, tmp_path):
    f = facts.parse_facts(facts.extract_payload(_fake_el9(tmp_path, CU_RC="1")))
    assert f["pending_known"] is False
    assert any("dnf check-update failed (exit 1)" in w for w in f["warnings"])
    ok = tmp_path / "ok"
    ok.mkdir()
    assert facts.parse_facts(facts.extract_payload(_fake_el9(ok, CU_RC="100")))["pending_known"] is True
