"""The rotation param is allowed for rotate_secret and for nothing else.

Credential rotation returned 502 for every attempt: inventory-service sends
`subject_credential_id` (which credential to rotate), and automation-service's
caller-param allowlist rejected it, because the allowlist is built from the
template's own declared keys and nothing declared that one.

The allowlist itself is correct and load-bearing — it is what closed the
webhook -> executor injection path. The fix therefore had to open exactly one
key, for exactly the flow that needs it, without giving webhook-driven
templates any new surface.

These tests pin both halves. The second one is the one that matters: a
regression here would not break anything visibly, it would quietly widen what
an attacker-influenceable payload may set.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

# Loaded straight from its path. An earlier version did
# sys.path.insert(0, automation-service) at import time, which left that
# directory on sys.path for the rest of the session — so a LATER test importing
# "app.*" resolved it to automation-service's app package and failed. Tests must
# not mutate global import state to reach a module; the failure showed up in a
# different file entirely, which is the worst way to debug one.
_SPEC_PATH = (
    Path(__file__).resolve().parent.parent
    / "services/automation-service/app/_params.py"
)
_spec = importlib.util.spec_from_file_location("_seyalrun_flow_params", _SPEC_PATH)
_params = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_params)

FLOW_PARAMS = _params.FLOW_PARAMS
RESERVED_PARAM_KEYS = _params.RESERVED_PARAM_KEYS
ParamNotAllowedError = _params.ParamNotAllowedError
allowed_param_keys_for = _params.allowed_param_keys_for
filter_caller_params = _params.filter_caller_params


def _tmpl(action_type: str, **kw):
    return SimpleNamespace(
        action_type=action_type,
        allowed_param_keys=kw.get("allowed_param_keys", []),
        default_params=kw.get("default_params", {}),
        survey_schema=kw.get("survey_schema", None),
    )


def test_rotate_secret_accepts_the_rotation_param():
    """The failure that made rotation return 502 for every call."""
    tmpl = _tmpl("rotate_secret")
    out = filter_caller_params(tmpl, {"subject_credential_id": "cred-123"})
    assert out == {"subject_credential_id": "cred-123"}


@pytest.mark.parametrize("action_type", ["ansible_playbook", "bash_script", "chain", "account_push", ""])
def test_other_templates_still_reject_it(action_type: str):
    """Scoped, not global. A webhook-driven template must not gain the key just
    because the rotation flow needed it."""
    with pytest.raises(ParamNotAllowedError):
        filter_caller_params(_tmpl(action_type), {"subject_credential_id": "cred-123"})


def test_flow_params_can_never_reopen_a_reserved_key():
    """script_content / playbook_path are the injection path this module closes.
    FLOW_PARAMS is unioned before the reserved-key subtraction, so even a
    mistaken entry there cannot re-open them — this asserts the ordering holds."""
    assert not (set().union(*FLOW_PARAMS.values()) & RESERVED_PARAM_KEYS)
    for action_type in FLOW_PARAMS:
        tmpl = _tmpl(action_type)
        assert not (allowed_param_keys_for(tmpl) & RESERVED_PARAM_KEYS)
        with pytest.raises(ParamNotAllowedError):
            filter_caller_params(tmpl, {"script_content": "rm -rf /"})


def test_reserved_and_control_keys_still_rejected_for_rotation():
    """Opening one key must not have relaxed the other guards on that path."""
    tmpl = _tmpl("rotate_secret")
    for bad in ({"playbook_path": "/etc/passwd"}, {"_internal": 1}, {"whatever": 1}):
        with pytest.raises(ParamNotAllowedError):
            filter_caller_params(tmpl, bad)
