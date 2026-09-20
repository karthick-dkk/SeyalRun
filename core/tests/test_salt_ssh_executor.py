"""salt-ssh roster generation and result interpretation, executed.

Three things here can fail silently and be believed:

1. The roster is generated from operator- and vault-influenced strings. Built by
   hand-formatting YAML, an address containing a newline appends whole roster
   entries — new targets nobody named. So it is structure-dumped and the address
   never forms the roster key.
2. salt-ssh exits 0 while individual targets fail. Reading process exit status
   would report a green job for a fleet that errored.
3. A host behind a gateway must not be reachable when the gateway cannot be
   jumped. Falling back to a direct connection would route around the bastion the
   zone topology exists to force traffic through — a silent downgrade, which is
   the worst kind.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "services/automation-service/app/plugins/executors/salt_ssh.py"


def _pure():
    """Exec the module's pure helpers — the executors themselves import the app package."""
    src = SRC.read_text()
    body = (
        "from __future__ import annotations\n"
        "import json, os\n"
        "from typing import Any\n"
        + src[src.index("_SSH_OPTIONS_BASE ="):src.index("async def _resolve_targets")]
    )
    ns: dict = {}
    exec(compile(body, str(SRC), "exec"), ns)  # noqa: S102 - our own source
    return ns


PURE = _pure()
build_roster = PURE["build_roster"]
proxy_jump_spec = PURE["proxy_jump_spec"]
summarize = PURE["summarize"]
salt_ssh_argv = PURE["salt_ssh_argv"]
master_config = PURE["master_config"]

KEY_CRED = {"username": "deploy", "secret_type": "ssh_key", "secret": {"private_key": "-----BEGIN-----"}}
PW_CRED = {"username": "ops", "secret_type": "password", "secret": {"password": "hunter2"}}


# ── roster shape ─────────────────────────────────────────────────────────────

def test_key_credentials_never_put_a_key_in_the_roster():
    roster, passwords = build_roster([({"id": "a", "ip": "10.0.0.1"}, KEY_CRED, None, "")], "/tmp/agent.sock", "/tmp/thin")
    entry = roster["host_a"]
    assert entry["priv"] == "agent-forwarding"
    assert "-----BEGIN-----" not in yaml.safe_dump(roster), "a private key must never reach the roster file"
    assert passwords == []
    assert f"IdentityAgent=/tmp/agent.sock" in entry["ssh_options"]


def test_password_credentials_are_returned_for_masking():
    roster, passwords = build_roster([({"id": "b", "ip": "10.0.0.2"}, PW_CRED, None, "")], "/tmp/a.sock", "/tmp/thin")
    assert roster["host_b"]["passwd"] == "hunter2"
    assert passwords == ["hunter2"], "the caller masks these; re-extracting them by string match would be fragile"


def test_the_address_cannot_alter_roster_structure():
    evil = "10.0.0.9\nhost_evil:\n  host: 1.2.3.4"
    roster, _ = build_roster([({"id": "c", "ip": evil}, KEY_CRED, None, "")], "/tmp/a.sock", "/tmp/thin")
    reparsed = yaml.safe_load(yaml.safe_dump(roster))
    assert list(reparsed.keys()) == ["host_c"], "a newline in an address must not append roster entries"
    assert reparsed["host_c"]["host"] == evil


def test_roster_key_is_synthetic_so_duplicate_addresses_stay_distinct():
    roster, _ = build_roster([
        ({"id": "one", "ip": "10.0.0.5"}, KEY_CRED, None, ""),
        ({"id": "two", "ip": "10.0.0.5"}, KEY_CRED, None, ""),
    ], "/tmp/a.sock", "/tmp/thin")
    assert sorted(roster) == ["host_one", "host_two"]


def test_port_falls_back_to_22_on_junk():
    roster, _ = build_roster([({"id": "d", "ip": "h", "port": "not-a-port"}, KEY_CRED, None, "")], "/s", "/t")
    assert roster["host_d"]["port"] == 22


def test_sudo_requires_a_tty_or_the_prompt_hangs_the_job():
    roster, passwords = build_roster([({"id": "e", "ip": "h"}, PW_CRED, "sudopw", "")], "/s", "/t")
    entry = roster["host_e"]
    assert entry["sudo"] is True
    assert entry["tty"] is True, "without a tty sudo prompts and the run hangs with nobody watching"
    assert "sudopw" in passwords


def test_proxy_jump_is_written_into_ssh_options_root_first():
    chain = [{"host": "gw1", "port": 22, "username": "ops"}, {"host": "gw2", "port": 2222, "username": ""}]
    spec = proxy_jump_spec(chain)
    assert spec == "ops@gw1:22,gw2:2222"
    roster, _ = build_roster([({"id": "f", "ip": "10.0.0.7"}, KEY_CRED, None, spec)], "/s", "/t")
    assert f"ProxyJump={spec}" in roster["host_f"]["ssh_options"]


def test_no_proxy_jump_option_when_there_is_no_chain():
    roster, _ = build_roster([({"id": "g", "ip": "h"}, KEY_CRED, None, "")], "/s", "/t")
    assert not any(o.startswith("ProxyJump") for o in roster["host_g"]["ssh_options"])


# ── targeting ────────────────────────────────────────────────────────────────

def test_targets_are_an_explicit_list_never_a_glob():
    argv = salt_ssh_argv("/conf", "/roster", ["host_a", "host_b"], raw_command="uptime")
    assert "-L" in argv and argv[argv.index("-L") + 1] == "host_a,host_b"
    assert "*" not in argv, "a glob taken from params is how a job reaches machines nobody named"
    assert argv[argv.index("-r") + 1] == "uptime"


def test_state_mode_applies_the_named_state_and_sends_no_raw_command():
    argv = salt_ssh_argv("/conf", "/roster", ["host_a"], state_name="seyalrun_job")
    assert argv[-2:] == ["state.apply", "seyalrun_job"]
    assert "-r" not in argv


def test_master_config_keeps_every_path_inside_the_run_tempdir():
    cfg = master_config("/tmp/run1", "/tmp/run1/states")
    for key in ("cachedir", "pki_dir", "log_file", "ssh_log_file"):
        assert cfg[key].startswith("/tmp/run1"), f"{key} escaping the tempdir fails on a read-only rootfs"
    assert cfg["file_roots"]["base"] == ["/tmp/run1/states"]


# ── result interpretation ────────────────────────────────────────────────────

def test_a_nonzero_retcode_fails_the_job_even_though_salt_ssh_exited_zero():
    ok, out = summarize({"host_a": {"stdout": "", "stderr": "boom", "retcode": 1}}, raw=True)
    assert ok is False
    assert "boom" in out


def test_raw_success_reports_stdout():
    ok, out = summarize({"host_a": {"stdout": "up 3 days", "stderr": "", "retcode": 0}}, raw=True)
    assert ok is True
    assert "up 3 days" in out


def test_a_failed_state_fails_the_job():
    payload = {"host_a": {"pkg_|-nginx": {"result": False, "comment": "no package"},
                          "file_|-conf": {"result": True}}}
    ok, out = summarize(payload, raw=False)
    assert ok is False
    assert "no package" in out


def test_all_states_passing_reports_ok_and_counts_changes():
    payload = {"host_a": {"pkg_|-nginx": {"result": True, "changes": {"new": "1.0"}},
                          "svc_|-nginx": {"result": True, "changes": {}}}}
    ok, out = summarize(payload, raw=False)
    assert ok is True
    assert "2 states, 1 changed" in out


def test_a_bare_error_string_is_a_failure_not_a_pass():
    ok, out = summarize({"host_a": "Salt request timed out"}, raw=False)
    assert ok is False
    assert "timed out" in out


def test_empty_result_is_a_failure():
    assert summarize({}, raw=True)[0] is False
    assert summarize(None, raw=False)[0] is False


def test_one_failing_target_fails_the_whole_run():
    payload = {"a": {"retcode": 0, "stdout": "ok"}, "b": {"retcode": 2, "stdout": "", "stderr": "nope"}}
    assert summarize(payload, raw=True)[0] is False


# ── source-pinned safety properties ──────────────────────────────────────────

def test_the_roster_file_is_written_0600():
    src = SRC.read_text()
    assert "0o600" in src, "the roster holds passwords for the run's duration"


def test_passwords_are_registered_for_masking_before_the_roster_is_written():
    src = SRC.read_text()
    assert src.index("_masking.register(pw)") < src.index("yaml.safe_dump(roster, fh")


def test_a_gateway_without_a_key_skips_the_host_instead_of_connecting_directly():
    src = SRC.read_text()
    resolve = src[src.index("async def _resolve_targets"):]
    assert "skipped rather than" in resolve
    # the `continue` must be inside the unusable-gateway branch, not a log-and-proceed
    assert resolve.index("if unusable:") < resolve.index("continue", resolve.index("if unusable:"))
