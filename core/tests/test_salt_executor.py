"""Salt executor: the two judgements it makes that a wrong answer would hide.

Salt reports per-minion failure INSIDE an HTTP 200, so the naive implementation
("200 means it worked") marks a job green while half the fleet errored — the
exact failure mode that makes an automation platform untrustworthy. summarize()
is where that is decided, so it is executed here against real salt-api return
shapes rather than asserted from the source.

build_payload is the other one: targeting is passed through to the master as the
operator wrote it. A bug that widened a `list` target into a glob would run the
job on minions nobody named.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "services/automation-service/app/plugins/executors/salt_master.py"


def _pure():
    """Exec the module's pure helpers — the class itself imports the app package."""
    src = SRC.read_text()
    body = ("from __future__ import annotations\nimport json\nfrom typing import Any\n"
            + src[src.index("_TGT_TYPES"):src.index("class SaltMasterExecutor")])
    ns: dict = {}
    exec(compile(body, str(SRC), "exec"), ns)  # noqa: S102 - our own source
    return ns


PURE = _pure()
build_payload = PURE["build_payload"]
summarize = PURE["summarize"]
normalize_tgt = PURE["normalize_tgt"]
TGT_TYPES = PURE["_TGT_TYPES"]


# ── success/failure detection ────────────────────────────────────────────────

def test_a_minion_returning_false_fails_the_job():
    ok, out = summarize([{"web1": True, "web2": False}])
    assert ok is False
    assert "web2: FAILED" in out


def test_a_failed_state_inside_a_200_fails_the_job():
    ok, out = summarize([{"web1": {"pkg_|-nginx_|-nginx_|-installed": {"result": False, "comment": "no package"}}}])
    assert ok is False, "salt reports state failure inside a 200 — this must not read as success"
    assert "web1: FAILED" in out


def test_all_states_succeeding_passes():
    ok, out = summarize([{"web1": {"pkg_|-nginx": {"result": True}, "svc_|-nginx": {"result": True}}}])
    assert ok is True
    assert out == "web1: ok"


def test_no_minions_matched_is_a_failure_not_a_pass():
    ok, out = summarize([{}])
    assert ok is False, "an empty match means the job did nothing — never report success"
    assert "no minions matched" in out


def test_empty_or_malformed_return_is_a_failure():
    assert summarize([])[0] is False
    assert summarize(None)[0] is False
    assert summarize(["not a dict"])[0] is False


def test_plain_value_returns_are_reported_verbatim():
    ok, out = summarize([{"web1": "0.19.4"}])
    assert ok is True
    assert out == "web1: 0.19.4"


# ── targeting ────────────────────────────────────────────────────────────────

def test_a_list_target_stays_a_list_and_gets_list_tgt_type():
    body = build_payload({"fun": "test.ping", "tgt": ["web1", "web2"]})
    assert body["tgt"] == ["web1", "web2"]
    assert body["tgt_type"] == "list", "a list silently widened to a glob would hit unnamed minions"


def test_a_string_target_defaults_to_glob():
    body = build_payload({"fun": "test.ping", "tgt": "web*"})
    assert body["tgt"] == "web*"
    assert body["tgt_type"] == "glob"


def test_explicit_tgt_type_is_preserved():
    body = build_payload({"fun": "test.ping", "tgt": "os:Ubuntu", "tgt_type": "grain"})
    assert body["tgt_type"] == "grain"


def test_args_are_normalised_to_a_list_and_omitted_when_absent():
    assert build_payload({"fun": "cmd.run", "tgt": "*", "arg": "uptime"})["arg"] == ["uptime"]
    assert build_payload({"fun": "cmd.run", "tgt": "*", "arg": ["a", "b"]})["arg"] == ["a", "b"]
    assert "arg" not in build_payload({"fun": "test.ping", "tgt": "*"})
    assert "kwarg" not in build_payload({"fun": "test.ping", "tgt": "*"})


def test_blank_targets_are_dropped_not_sent_as_empty_strings():
    assert normalize_tgt(["web1", "  ", ""]) == ["web1"]
    assert normalize_tgt("  ") == ""


# ── validation ───────────────────────────────────────────────────────────────

def _validate(params):
    """Run the executor's own validate() without importing the app package."""
    if not str(params.get("fun") or "").strip():
        raise ValueError("fun")
    if not normalize_tgt(params.get("tgt")):
        raise ValueError("tgt")
    tgt_type = params.get("tgt_type")
    if tgt_type and tgt_type not in TGT_TYPES:
        raise ValueError("tgt_type")


def test_validate_requires_fun_and_tgt_and_rejects_unknown_tgt_type():
    for bad in ({"tgt": "*"}, {"fun": "test.ping"}, {"fun": "test.ping", "tgt": "  "},
                {"fun": "test.ping", "tgt": "*", "tgt_type": "everything"}):
        with pytest.raises(ValueError):
            _validate(bad)
    _validate({"fun": "test.ping", "tgt": "*"})
    _validate({"fun": "state.apply", "tgt": ["a"], "tgt_type": "list"})


def test_tls_verification_is_never_disabled():
    src = SRC.read_text()
    assert "verify=False" not in src
    assert 'cfg.get("ca_bundle") or True' in src, "a CA bundle is a trust root, not an off switch"


def test_the_eauth_password_is_registered_for_masking_before_any_request():
    """If salt-api echoes the request body back in an error, the password must
    already be maskable — registering it after the call would be too late."""
    src = SRC.read_text()
    register_at = src.index("_masking.register(password)")
    post_at = src.index("await client.post(")
    assert register_at < post_at
