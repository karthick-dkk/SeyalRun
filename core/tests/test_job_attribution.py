"""Job-run attribution and the approval separation-of-duties gate (F-2).

Loads automation-service/app/_attribution.py itself rather than a mirror. The
mirror this replaces (in test_audit_coverage.py) fed in bare UUIDs, but every real
run stores ``user:<uuid>``; the real code passed that 41-char marker into
za_audit_logs.user_id (String(36)), so user-triggered job audit rows were rejected
and dropped while the mirror test stayed green.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUTO = ROOT / "services/automation-service/app"

_spec = importlib.util.spec_from_file_location("_attribution_under_test", AUTO / "_attribution.py")
attr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attr)

UID = "6f1c2f8e-0b1a-4a55-9a1e-3c0d5f6a7b88"
AUDIT_USER_ID_MAX = 36  # identity-service ZAAuditLog.user_id = String(36)


# ── attribution ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("marker", [f"user:{UID}", f"manual_trigger:binding-1:{UID}", UID])
def test_human_triggers_resolve_to_the_bare_user_id(marker):
    assert attr.actor_user_id(marker) == UID


@pytest.mark.parametrize("marker", [
    "schedule:7", "zabbix_event:10453", "zabbix:10453", "zabbix_manual:10453",
    "system:rotation_due_sweep", "", None,
])
def test_machine_triggers_are_not_attributed_to_a_user(marker):
    assert attr.actor_user_id(marker) is None


def test_every_real_marker_fits_the_audit_column():
    """The property that actually broke: what lands in user_id must fit the column."""
    for marker in (f"user:{UID}", f"manual_trigger:{UID}:{UID}", UID):
        out = attr.actor_user_id(marker)
        assert out is None or len(out) <= AUDIT_USER_ID_MAX, (marker, out)


def test_a_user_id_beginning_with_a_word_like_scheduled_is_not_dropped():
    assert attr.actor_user_id("scheduled-user-42") == "scheduled-user-42"


def test_markers_written_by_the_code_are_the_ones_handled():
    """Every triggered_by f-string in the codebase must use a kind this module knows,
    so a new trigger kind cannot silently start writing raw markers into audit."""
    known = {"user", "manual_trigger", "schedule", "zabbix_event", "zabbix", "zabbix_manual", "system"}
    import re
    kinds = set()
    for f in (ROOT / "services").rglob("*.py"):
        for m in re.finditer(r'triggered_by["\']?\s*[:=]\s*f?["\'](\w+):', f.read_text()):
            kinds.add(m.group(1))
        for m in re.finditer(r'_dispatch_\w+\([^)]*f"(\w+):', f.read_text()):
            kinds.add(m.group(1))
    assert kinds, "found no triggered_by markers — the scan is broken"
    assert kinds <= known, f"unhandled trigger kinds: {kinds - known}"


def test_runner_attributes_through_the_shared_helper():
    src = (AUTO / "runner.py").read_text()
    assert "from ._attribution import actor_user_id" in src
    assert "return actor_user_id(triggered_by)" in src


# ── self-approval (F-2) ──────────────────────────────────────────────────────

def test_requester_is_recognised_in_every_human_marker_form():
    assert attr.is_requester(f"user:{UID}", UID)
    assert attr.is_requester(f"manual_trigger:b:{UID}", UID)
    assert attr.is_requester(UID, UID)


def test_a_different_user_is_not_the_requester():
    assert not attr.is_requester(f"user:{UID}", "someone-else")
    assert not attr.is_requester("schedule:7", UID)


def test_missing_caller_identity_fails_closed():
    assert attr.is_requester(f"user:{UID}", "")
    assert attr.is_requester("schedule:7", None)


def _func(src: str, name: str) -> str:
    tree = ast.parse(src)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    return ast.get_source_segment(src, node)


def test_approve_blocks_self_approval_and_audits_fail_closed_before_dispatch():
    src = (AUTO / "api/job_runs.py").read_text()
    fn = _func(src, "approve_run")
    i_block = fn.index("is_requester(run.triggered_by, user_id")
    i_audit = fn.index('action="job.approve"')
    i_status = fn.index('run.status = "pending"')
    i_dispatch = fn.index("_runner.execute(")
    assert i_block < i_audit < i_status < i_dispatch, "order must be: SoD check, audit, state change, dispatch"
    assert "critical=True" in fn[i_audit:i_status], "approval audit must be fail-closed"
    assert "AuditWriteError" in fn[i_audit:i_status]


def test_reject_is_audited():
    fn = _func((AUTO / "api/job_runs.py").read_text(), "reject_run")
    assert 'action="job.reject"' in fn


# ── machine triggers: whoever configured them may not approve their runs ─────
# A scheduled or Zabbix-triggered run on a production host is held for approval,
# and its triggered_by names the schedule/event, not a person. The schedule's or
# binding's author and last editor travel with the run as ACCOUNTABLE_PARAM.

def test_schedule_author_and_editor_cannot_approve_its_run():
    acct = attr.accountable_ids("author-1", *attr.with_editor(["author-1"], "editor-2"))
    assert attr.is_requester("schedule:7", "author-1", acct)
    assert attr.is_requester("schedule:7", "editor-2", acct)
    assert not attr.is_requester("schedule:7", "third-party", acct)


def test_accountable_ids_drops_blanks_and_duplicates():
    assert attr.accountable_ids("a", None, "", "a", "b") == ["a", "b"]


def test_legacy_trigger_with_no_recorded_author_stays_approvable():
    """Documented residual (R-14): a schedule/binding saved before 2.0.2 has no
    author, so any approver may approve — refusing everyone would strand the run."""
    assert not attr.is_requester("schedule:7", "anyone", [])


def test_every_machine_dispatch_path_attaches_accountability():
    sched = (AUTO / "scheduler.py").read_text()
    assert "params[ACCOUNTABLE_PARAM] = accountable_ids(sched.created_by, *(sched.editor_ids or []))" in sched
    # written AFTER params_override is merged, so the override cannot clear it
    i_merge = sched.index("**sched.params_override")
    assert sched.index("params[ACCOUNTABLE_PARAM]") > i_merge

    internal = (AUTO / "api/internal.py").read_text()
    assert "accountable_user_ids: list[str] = []" in internal
    assert internal.index("params[ACCOUNTABLE_PARAM]") > internal.index("filter_caller_params(tmpl, payload.params)")

    zbx = ROOT / "services/zabbix-integration-service/app/api"
    for f in ("webhook.py", "trigger_bindings.py"):
        assert '"accountable_user_ids": binding_accountable_ids(binding)' in (zbx / f).read_text(), f


def test_callers_cannot_forge_or_clear_the_accountability_param():
    """Caller params may not set any _-prefixed control key, which is what keeps
    ACCOUNTABLE_PARAM service-asserted."""
    assert attr.ACCOUNTABLE_PARAM.startswith("_")
    src = (AUTO / "_params.py").read_text()
    assert 'control = sorted(k for k in supplied if k.startswith("_"))' in src


def test_schedule_and_binding_writes_record_the_editor():
    sch = (AUTO / "api/schedules.py").read_text()
    assert "created_by=user_id, editor_ids=[user_id]" in sch
    assert "s.editor_ids = with_editor(s.editor_ids, user_id)" in sch
    tb = (ROOT / "services/zabbix-integration-service/app/api/trigger_bindings.py").read_text()
    assert "created_by=user_id, editor_ids=[user_id]" in tb
    assert "b.editor_ids = with_editor(b.editor_ids, user_id)" in tb


def test_a_later_edit_never_frees_an_earlier_editor():
    """The overwrite bypass: C creates, A makes the risky edit, B touches it later.
    A must still be barred from approving."""
    editors = attr.with_editor(None, "creator-C")
    editors = attr.with_editor(editors, "risky-A")
    editors = attr.with_editor(editors, "later-B")
    editors = attr.with_editor(editors, "risky-A")  # re-edit does not duplicate
    assert editors == ["creator-C", "risky-A", "later-B"]
    acct = attr.accountable_ids("creator-C", *editors)
    assert attr.is_requester("schedule:7", "risky-A", acct)


def test_approve_passes_accountable_ids():
    fn = _func((AUTO / "api/job_runs.py").read_text(), "approve_run")
    assert "is_requester(run.triggered_by, user_id, accountable)" in fn
