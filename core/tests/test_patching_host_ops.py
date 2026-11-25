"""Patching on the host side (3.0 M3): argument validation, the removal guard run
against recorded package-manager output, and the launcher's run-once contract."""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parent.parent
AUTO = CORE / "services/automation-service/app"
EXEC = AUTO / "plugins/executors"
FIX = Path(__file__).parent / "fixtures/patching"
WORKER = (EXEC / "package_ops.sh").read_text()

_spec = importlib.util.spec_from_file_location("host_ops_under_test", AUTO / "_host_ops.py")
ops = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ops)

KEY = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nAAAA\n=abcd\n-----END PGP PUBLIC KEY BLOCK-----"
REPO = {"slug": "m", "family": "deb", "base_url": "http://mirror/deb", "suite": "bookworm", "components": "main", "gpg_key": KEY}


@pytest.mark.parametrize("name", ["-y", "--allow-downgrades", "a b", "a;id", "$(id)", "a\nb", "../x", "a=1", "", "a:b", "*", "`id`"])
def test_bad_package_names_are_refused(name):
    with pytest.raises(ops.HostOpError):
        ops.package_argv("upgrade", {"packages": [{"name": name}]})


@pytest.mark.parametrize("field,value", [("arch", "amd64;id"), ("version", "1 2"), ("version", "-1$(id)"), ("arch", "-x")])
def test_bad_arch_and_version_are_refused(field, value):
    with pytest.raises(ops.HostOpError):
        ops.package_argv("install", {"packages": [{"name": "curl", field: value}]})


def test_package_arguments():
    assert ops.package_argv("upgrade", {"packages": [{"name": "libssl3", "arch": "amd64"}]}) == ["upgrade", "host", "libssl3:amd64"]
    assert ops.package_argv("pin", {"packages": [{"name": "curl", "version": "1:2~3+b"}], "repo_mode": "managed"})[1:] == ["managed", "curl=1:2~3+b"]
    for op, args in (("pin", {"packages": [{"name": "curl"}]}), ("rollback", {"packages": [{"name": "curl"}]}),
                     ("upgrade", {"packages": []}), ("upgrade", {"packages": [{"name": "a"}], "extra": 1}),
                     ("shell", {}), ("upgrade", {"packages": [{"name": "a"}], "repo_mode": "x"})):
        with pytest.raises(ops.HostOpError):
            ops.package_argv(op, args)


@pytest.mark.parametrize("name", sorted(ops.PROTECTED))
def test_protected_packages_cannot_be_named_for_removal(name):
    for op in ("remove", "purge"):
        with pytest.raises(ops.HostOpError):
            ops.package_argv(op, {"packages": [{"name": name}]})


@pytest.mark.parametrize("change", [{"gpg_key": ""}, {"gpg_key": "trusted=yes"}, {"base_url": "http://u:p@m/x"},
                                    {"base_url": "file:///x"}, {"base_url": "http://m/x\nTrusted: yes"}, {"suite": "a b"},
                                    {"suite": "x\nTrusted: yes"}, {"slug": "../x"}, {"components": ""}, {"family": "apk"}])
def test_unsafe_mirrors_are_refused(change):
    with pytest.raises(ops.HostOpError):
        ops.package_argv("repo_apply", {"repos": [{**REPO, **change}]})


def test_mirrors_are_always_signature_checked():
    for fam in ("deb", "rpm", "zypp"):
        body = ops.render_repo({**REPO, "family": fam})[1][2]
        assert ("Signed-By:" in body) if fam == "deb" else ("gpgcheck=1" in body and "gpgkey=" in body)
    for bad in ("allow-unauthenticated", "--nogpgcheck", "no-gpg-checks", "--gpg-auto-import-keys\"", "trusted=yes"):
        code = "\n".join(ln.split(" #")[0] for ln in WORKER.splitlines() if not ln.lstrip().startswith("#"))
        assert bad not in code


def test_oversized_mirror_set_is_refused_before_ssh():
    big = KEY.replace("AAAA", "A" * 30000)
    with pytest.raises(ops.HostOpError, match="too large"):
        ops.package_argv("repo_apply", {"repos": [{**REPO, "slug": f"m{i}", "gpg_key": big} for i in range(4)]})


def test_worker_and_api_agree_on_protected_packages():
    rx = re.search(r"^PROTECTED_RX='([^']+)'", WORKER, re.MULTILINE).group(1)
    assert set(rx.split("|")) == set(ops.PROTECTED)


# ── removal guard, on recorded output ────────────────────────────────────────

def guard(pm: str, output: str, rc: int) -> subprocess.CompletedProcess:
    fn = re.search(r"^guard_removal\(\) \{.*?^\}", WORKER, re.MULTILINE | re.DOTALL).group(0)
    rx = re.search(r"^PROTECTED_RX=.*$", WORKER, re.MULTILINE).group(0)
    script = f'pm={pm}\n{rx}\n{fn}\nfake() {{ cat "$F"; return {rc}; }}\nguard_removal fake\n'
    path = os.environ.get("TMPDIR", "/tmp") + f"/guard-{os.getpid()}.txt"
    Path(path).write_text(output)
    return subprocess.run(["bash", "-c", script], env={**os.environ, "F": path}, capture_output=True, text=True)


@pytest.mark.parametrize("pm,name,rc", [("dnf", "dnf_remove_pam.txt", 1), ("zypper", "zypper_remove_libz1.txt", 0)])
def test_guard_refuses_collateral_removal(pm, name, rc):
    r = guard(pm, (FIX / name).read_text(), rc)
    assert r.returncode == 1 and "protected package" in r.stdout, r.stdout


def test_guard_refuses_a_failed_dry_run_and_unknown_packages():
    assert guard("apt", (FIX / "apt_remove_failed.txt").read_text(), 100).returncode == 1
    assert guard("dnf", "No match for argument: nosuch\n", 1).returncode == 1
    assert guard("zypper", "", 104).returncode == 1


def test_guard_allows_a_plain_removal():
    assert guard("apt", "Remv tree [2.1.0-1]\n", 0).returncode == 0
    assert guard("dnf", "Removing:\n tree  aarch64  1.8  @base  113 k\nOperation aborted.\n", 1).returncode == 0
    assert guard("zypper", "The following package is going to be REMOVED:\n  tree\n", 0).returncode == 0


# ── metadata refresh: an index that could not be verified is not a refresh ───

def refresh(output: str, rc: int, tmp_path) -> int:
    fn = re.search(r"^refresh\(\) \{.*?^\}", WORKER, re.MULTILINE | re.DOTALL).group(0)
    script = f'pm=apt; dir="{tmp_path}"\napt_fake() {{ printf "%s\\n" "$OUT"; return {rc}; }}\nAPT=(apt_fake)\n{fn}\nrefresh\n'
    return subprocess.run(["bash", "-c", script], env={**os.environ, "OUT": output}, capture_output=True).returncode


@pytest.mark.parametrize("line", [
    "W: An error occurred during the signature verification. The repository is not updated and the previous index files will be used.",
    "W: Failed to fetch http://m/InRelease  Could not connect", "W: Some index files failed to download. They have been ignored, or old ones used instead.",
    "W: GPG error: http://m bookworm InRelease: NO_PUBKEY 6ED0E7B82643E131"])
def test_unverified_metadata_fails_the_refresh(line, tmp_path):
    assert refresh(line, 0, tmp_path) != 0


def test_clean_or_harmless_refresh_passes(tmp_path):
    assert refresh("", 0, tmp_path) == 0
    assert refresh("W: Target Packages is configured multiple times", 0, tmp_path) == 0
    assert refresh("E: boom", 100, tmp_path) == 100


# ── executors, templates, approval ───────────────────────────────────────────

def test_lost_connection_is_not_success():
    src = (AUTO / "_ssh_exec.py").read_text()
    ns: dict = {}
    for node in ast.parse(src).body:
        if (isinstance(node, ast.FunctionDef) and node.name == "exit_code") or \
                (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "NO_EXIT_STATUS"):
            exec(compile(ast.Module([node], []), "x", "exec"), ns)  # noqa: S102
    assert ns["exit_code"](None) != 0 and ns["exit_code"](0) == 0 and ns["exit_code"](3) == 3
    assert "exit_status or 0" not in src


def test_host_operations_cannot_be_rerun_or_run_by_hand():
    assert {ops.PACKAGE_ACTION, ops.REBOOT_ACTION} <= set(ops.NO_RERUN_ACTIONS)
    assert "NO_RERUN_ACTIONS" in (AUTO / "api/job_runs.py").read_text()
    assert "run by Posture" in (EXEC / "package_ops.py").read_text()


def test_rollout_approval_is_checked_and_recorded():
    src = (AUTO / "api/internal.py").read_text()
    body = re.search(r"async def _production_gated.*?\n    return False\n", src, re.DOTALL).group(0)
    assert body.index("approval is None") < body.index("accountable_user_ids") < body.index("critical=True") < body.index("503")
    for route in ("create_posture_facts_runs", "create_host_op_run", "create_hook_run"):
        m = re.search(rf"async def {route}\(.*?(?=\n@router|\Z)", src, re.DOTALL)
        assert m and "_production_gated(" in m.group(0), route


@pytest.mark.skipif(not shutil.which("bash"), reason="bash")
@pytest.mark.parametrize("name", ["package_ops.sh", "host_reboot.sh", "host_snapshot.sh", "host_op_launcher.sh", "posture_facts.sh"])
def test_scripts_parse(name):
    assert subprocess.run(["bash", "-n", str(EXEC / name)], capture_output=True).returncode == 0


def test_reboot_records_its_result_before_rebooting():
    s = (EXEC / "host_reboot.sh").read_text()
    assert s.index('mv -f "$dir/rc.tmp"') < s.index('"${how[@]}"')


# ── snapshot is read-only ────────────────────────────────────────────────────

def test_snapshot_never_changes_the_host():
    s = (EXEC / "host_snapshot.sh").read_text()
    code = "\n".join(ln for ln in s.splitlines() if not ln.lstrip().startswith("#"))
    assert not re.search(r"\b(rm|mv|sudo|apt-get|dnf|yum|reboot|shutdown|systemctl (start|stop|restart))\b", code)
