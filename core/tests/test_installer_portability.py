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


# ── repo URL and offline install ─────────────────────────────────────────────

def test_repo_url_matches_the_actual_repository():
    """The default pointed at karthick-dkk/seyalrun_zabbix, which does not exist
    (the repo is karthick-dkk/SeyalRun) — so every default fetch hung until
    timeout. This is the bug that blocked a real deploy."""
    src = INSTALL.read_text()
    assert "seyalrun_zabbix" not in src, "the old, non-existent repo name is still referenced"
    assert "karthick-dkk/SeyalRun/main" in src


def test_db_init_script_is_placed_where_compose_mounts_it():
    """The DB compose mounts ./core/docker-init/<engine>. Fetching the init
    script to a bare docker-init/ (the old path) means the per-service databases
    are never created and every migration fails against a missing database."""
    src = INSTALL.read_text()
    assert 'fetch "core/docker-init/postgres/init-dbs.sh"' in src
    db = (ROOT / "docker-compose.db.yml").read_text()
    assert "./core/docker-init/postgres" in db, "compose mount path changed — update the fetch"


def test_offline_image_archive_is_supported():
    """A locked-down host cannot pull. SEYALRUN_IMAGE_ARCHIVE loads a
    docker-save tarball instead — the path an air-gapped staging box needs."""
    src = INSTALL.read_text()
    assert "SEYALRUN_IMAGE_ARCHIVE" in src
    block = src[src.index('if [[ -n "${SEYALRUN_IMAGE_ARCHIVE'):][:700]
    assert "docker load" in block


def test_staged_files_are_not_refetched():
    """So an operator can scp the files and run the installer with no GitHub
    access at all, not just no Docker Hub access."""
    src = INSTALL.read_text()
    fn = src[src.index("fetch() {"):]
    fn = fn[: fn.index("}")]
    assert '[[ -f "$2" ]]' in fn, "fetch must skip a file that is already present"


# ── edge port override + pre-flight conflict check ───────────────────────────

def test_edge_ports_are_overridable():
    """A host often already runs something on 8080/8443. Both must be settable."""
    src = INSTALL.read_text()
    assert "SEYALRUN_HTTP_PORT" in src and "SEYALRUN_HTTPS_PORT" in src
    assert 's|^EDGE_HTTP_PORT=.*|EDGE_HTTP_PORT=' in src
    assert 's|^EDGE_HTTPS_PORT=.*|EDGE_HTTPS_PORT=' in src


def test_port_conflict_fails_early_and_names_the_fix():
    """The edge-proxy is the last container to start, so a clash otherwise
    surfaces as a cryptic bind error after the whole stack is up. It must fail in
    step 5 and name the override variable."""
    src = INSTALL.read_text()
    assert "port_taken" in src
    block = src[src.index("for pair in"):][:700]
    assert "fail " in block and "already in use" in block
    assert "SEYALRUN_HTTP_PORT" in block or "${var}" in block, "the error must name the override var"
    # and it must run before Step 6 (pull/load), not after the stack is up
    assert src.index("port_taken") < src.index("[6/8]")


def test_missing_ss_and_lsof_does_not_block_install():
    """A host with neither tool should skip the check, not fail every install."""
    src = INSTALL.read_text()
    fn = src[src.index("port_taken() {"):]
    fn = fn[: fn.index("\n}")]
    assert "return 1" in fn, "no probe tool available must mean 'assume free', not error"


# ── /opt base path + lifecycle control script ────────────────────────────────

def test_default_install_dir_is_opt_seyalrun():
    src = INSTALL.read_text()
    assert 'INSTALL_DIR="${SEYALRUN_DIR:-/opt/seyalrun}"' in src, \
        "the base install path must default to /opt/seyalrun"


def test_opt_creation_uses_privilege_and_hands_over_ownership():
    """/opt is root-owned. It must be created with privilege and chowned to the
    user, or every later write (.env, TLS, compose) would need sudo."""
    src = INSTALL.read_text()
    assert "priv mkdir -p" in src
    assert "priv chown" in src


def test_privilege_helper_supports_unattended_sudo():
    """A host whose sudo needs a password must still install non-interactively
    when SUDO_PASSWORD is provided."""
    src = INSTALL.read_text()
    fn = src[src.index("priv() {"):]
    fn = fn[: fn.index("\n}")]
    assert "SUDO_PASSWORD" in fn and "sudo -S" in fn


def test_control_script_is_generated_with_lifecycle_commands():
    src = INSTALL.read_text()
    assert 'CTL="${INSTALL_DIR}/seyalrunctl"' in src
    block = src[src.index("cat > \"$CTL\""):][:900]
    for verb in ("start)", "stop)", "restart)", "status", "logs)"):
        assert verb in block, f"control script missing '{verb}'"
    assert "chmod +x" in src[src.index("cat > \"$CTL\""):][:1100]


def test_control_script_embeds_the_actual_compose_invocation():
    """It must match how THIS install brought the stack up (dockerized vs
    external DB, chosen ports), not a hardcoded guess."""
    src = INSTALL.read_text()
    block = src[src.index("cat > \"$CTL\""):][:900]
    assert "COMPOSE=(${COMPOSE[@]})" in block, "the control script must embed the computed COMPOSE array"


def test_control_script_is_put_on_path():
    src = INSTALL.read_text()
    assert "/usr/local/bin/seyalrunctl" in src
