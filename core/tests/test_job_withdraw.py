"""A requester may withdraw (reject) their own pending run whatever their role; anyone
else needs the approver role. Runs the real reject_run() and _can_approve() from
job_runs.py, taken out of the module so no web framework or database is needed."""
from __future__ import annotations

import ast
import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

AUTO = Path(__file__).resolve().parent.parent / "services/automation-service/app"
_spec = importlib.util.spec_from_file_location("_attribution_withdraw", AUTO / "_attribution.py")
attr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attr)

UID = "6f1c2f8e-0b1a-4a55-9a1e-3c0d5f6a7b88"


class HTTPError(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(detail)
        self.status_code = status_code


def reject(run, role, user_id, approver_role=None):
    tree = ast.parse((AUTO / "api/job_runs.py").read_text())
    keep = []
    for n in tree.body:
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name in ("reject_run", "_can_approve"):
            n.decorator_list, n.args.defaults, n.returns = [], [], None
            for a in n.args.args:
                a.annotation = None
            keep.append(n)
    audits = []

    class Session:
        async def get(self, model, _id):
            return NS(name="t", approver_role=approver_role) if model == "tmpl" else run

        async def commit(self):
            pass

    async def log_action(**kw):
        audits.append(kw)

    ns = {"HTTPException": HTTPError, "status": NS(HTTP_403_FORBIDDEN=403), "ZAJobRun": "run", "ZAJobTemplate": "tmpl",
          "datetime": datetime, "timezone": timezone, "actor_user_id": attr.actor_user_id,
          "audit": NS(log_action=log_action)}
    exec(compile(ast.Module(keep, []), "job_runs", "exec"), ns)
    out = asyncio.run(ns["reject_run"]("r1", NS(reason="withdraw"), role, user_id, "u", Session()))
    return out, audits


def pending(triggered_by):
    return NS(id="r1", status="pending_approval", job_template_id="t", params={}, triggered_by=triggered_by, ended_at=None)


@pytest.mark.parametrize("marker", [f"user:{UID}", f"manual_trigger:b:{UID}", UID])
def test_support_requester_withdraws_own_run(marker):
    run = pending(marker)
    out, audits = reject(run, "support", UID)
    assert out["status"] == "rejected" and run.status == "rejected"
    assert audits and audits[0]["action"] == "job.reject"


@pytest.mark.parametrize("by,uid", [(f"user:{UID}", "someone-else"), ("schedule:7", UID), (f"user:{UID}", ""), (f"user:{UID}", None)])
def test_others_without_the_approver_role_cannot_reject(by, uid):
    run = pending(by)
    with pytest.raises(HTTPError) as e:
        reject(run, "support", uid)
    assert e.value.status_code == 403 and run.status == "pending_approval"


def test_an_admin_can_still_reject_someone_elses_run():
    run = pending("user:someone-else")
    out, _ = reject(run, "admin", UID)
    assert out["status"] == "rejected"


def test_superadmin_only_template_needs_superadmin_unless_own():
    with pytest.raises(HTTPError):
        reject(pending("user:x"), "admin", UID, approver_role="superadmin")
    out, _ = reject(pending(f"user:{UID}"), "admin", UID, approver_role="superadmin")
    assert out["status"] == "rejected"
