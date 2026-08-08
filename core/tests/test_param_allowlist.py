"""T5 / T11 invariant: caller-supplied job params can never choose the code that
runs on a host. script_content/playbook_path are template-only; unknown and
internal control keys are rejected."""

from __future__ import annotations

import pytest


@pytest.fixture
def params(load_module):
    return load_module("services/automation-service/app/_params.py", "za_params")


class _Tmpl:
    allowed_param_keys = ["env"]
    default_params = {"region": "us"}
    survey_schema = {"fields": [{"name": "ticket"}]}
    script_content = "echo hello"
    playbook_path = ""


def test_reserved_script_content_rejected(params):
    with pytest.raises(params.ParamNotAllowedError):
        params.filter_caller_params(_Tmpl(), {"script_content": "rm -rf /"})


def test_reserved_playbook_path_rejected(params):
    with pytest.raises(params.ParamNotAllowedError):
        params.filter_caller_params(_Tmpl(), {"playbook_path": "/etc/evil.yml"})


def test_internal_control_key_rejected(params):
    with pytest.raises(params.ParamNotAllowedError):
        params.filter_caller_params(_Tmpl(), {"_credential_id": "attacker-cred"})


def test_unknown_key_rejected(params):
    with pytest.raises(params.ParamNotAllowedError):
        params.filter_caller_params(_Tmpl(), {"totally_unknown": "1"})


def test_allowlisted_keys_pass(params):
    # env (explicit), region (default_params), ticket (survey field) all allowed.
    out = params.filter_caller_params(_Tmpl(), {"env": "prod", "region": "eu", "ticket": "T-9"})
    assert out == {"env": "prod", "region": "eu", "ticket": "T-9"}


def test_template_code_params_are_template_sourced(params):
    code = params.template_code_params(_Tmpl())
    assert code == {"script_content": "echo hello"}  # empty playbook_path omitted


def test_empty_params_ok(params):
    assert params.filter_caller_params(_Tmpl(), None) == {}
