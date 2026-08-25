"""The installer detects the machine it is on instead of assuming one.

It previously assumed both: no architecture check at all, and Docker either
present or the install stopped with a link. On an unsupported arch that surfaced
eight steps later as a docker-pull manifest error, which reads like a network
problem rather than "these images do not exist for your CPU".

Distribution matching uses ID *and* ID_LIKE. Matching only ID means every
derivative — Rocky, Alma, Oracle, Pop!_OS — is unsupported until someone adds it
by hand, and they all behave like a family that is already handled.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL = ROOT / "install.sh"


def test_installer_exists_and_is_valid_shell():
    assert INSTALL.exists()
    subprocess.run(["bash", "-n", str(INSTALL)], check=True)


# ── architecture ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("machine,expect", [
    ("x86_64", "amd64"), ("amd64", "amd64"),
    ("aarch64", "arm64"), ("arm64", "arm64"),
])
def test_both_published_architectures_are_recognised(machine: str, expect: str):
    """Images are published linux/amd64 and linux/arm64; both must map."""
    src = INSTALL.read_text()
    block = src[src.index('case "$(uname -m)" in'):]
    block = block[: block.index("esac")]
    assert machine in block, f"{machine} is not matched"
    assert expect in block


def test_unsupported_architecture_fails_early():
    """Before eight steps of setup, not at the first pull."""
    src = INSTALL.read_text()
    block = src[src.index('case "$(uname -m)" in'):]
    block = block[: block.index("esac")]
    assert "fail" in block and "unsupported architecture" in block
    # Before anything is DOWNLOADED — REPO_RAW_BASE is merely defined at the top,
    # so comparing against its definition proved nothing (my first version did).
    assert src.index('case "$(uname -m)" in') < src.index('fetch "docker-compose.prod.yml"')


# ── distribution ─────────────────────────────────────────────────────────────

def _resolve_pkg(idv: str, id_like: str) -> str:
    """Run the installer's own case statement, rather than restating it here."""
    src = INSTALL.read_text()
    case = src[src.index('case " ${ID:-} ${ID_LIKE:-} " in'):]
    case = case[: case.index("esac") + 4]
    script = f'ID="{idv}"; ID_LIKE="{id_like}"; PKG=""\n{case}\necho "$PKG"'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("idv,id_like,expect", [
    ("ubuntu", "debian", "apt"),
    ("debian", "", "apt"),
    ("rhel", "", "dnf"),
    ("centos", "rhel fedora", "dnf"),
    ("fedora", "", "dnf"),
    ("amzn", "fedora", "dnf"),                     # Amazon Linux 2023
    ("amzn", "centos rhel fedora", "dnf"),         # Amazon Linux 2
    ("rocky", "rhel centos fedora", "dnf"),        # derivative, via ID_LIKE only
    ("almalinux", "rhel centos fedora", "dnf"),
    ("opensuse-leap", "suse", "zypper"),
])
def test_requested_platforms_resolve_a_package_manager(idv: str, id_like: str, expect: str):
    assert _resolve_pkg(idv, id_like) == expect, f"{idv} resolved to {_resolve_pkg(idv, id_like)!r}"


def test_derivatives_resolve_through_id_like_alone():
    """Rocky and Alma name themselves; only ID_LIKE says what they behave like."""
    assert _resolve_pkg("rocky", "rhel centos fedora") == "dnf"
    assert _resolve_pkg("pop", "ubuntu debian") == "apt"


def test_old_releases_fall_back_from_dnf_to_yum():
    """Amazon Linux 2 and CentOS 7 have yum and no dnf; resolving to a command
    that is not there would fail at install time, not detection time."""
    src = INSTALL.read_text()
    assert re.search(r'PKG="dnf".*command -v dnf.*PKG="yum"', src, re.S), \
        "expected a dnf->yum fallback for releases that predate dnf"


# ── how Docker gets installed ────────────────────────────────────────────────

def test_docker_is_installed_from_distribution_packages():
    """Not by piping get.docker.com into a shell. That adds a third-party repo
    and runs unreviewed remote code as root, which is a large thing to do
    silently inside another installer."""
    # Comment-stripped: the script explains WHY it avoids get.docker.com, and a
    # whole-file check reports that explanation as the defect. That mistake has
    # now been made repeatedly in this work — checks written against text that
    # merely mentions the thing.
    code = "\n".join(l for l in INSTALL.read_text().split("\n") if not l.lstrip().startswith("#"))
    assert "get.docker.com" not in code
    assert "pkg_install docker" in code


def test_automatic_install_can_be_declined():
    src = INSTALL.read_text()
    assert "SEYALRUN_NO_INSTALL" in src, "there must be a way to opt out of package installs"


def test_docker_is_checked_for_reachability_not_just_presence():
    """A stopped daemon, or a user outside the docker group, looks exactly like a
    working install until the first pull fails."""
    src = INSTALL.read_text()
    assert "docker info" in src
    assert "docker group" in src or "usermod -aG docker" in src
