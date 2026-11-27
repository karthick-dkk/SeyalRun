"""Re-running a job never skips an approval.

Runs the real rerun() and cancel_run() from job_runs.py, taken out of the module so that
no web framework or database is needed.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from libs import adminapproval

AUTO = Path(__file__).resolve().parent.parent / "services/automation-service/app"
_spec = importlib.util.spec_from_file_location("_attribution_rerun", AUTO / "_attribution.py")
attr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(attr)


def _approval_helpers() -> dict:
    """The real caller_waiver() and constants from app/_approval.py, reading the switch
    from the environment the way the service's Settings would."""
    tree = ast.parse((AUTO / "_approval.py").read_text())
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in ("admin_approval_required", "caller_waiver"))
            or (isinstance(n, ast.Assign) and n.targets[0].id in ("WAIVER_PARAM", "PRODUCTION", "TEMPLATE"))]
    ns = {"approval_required": adminapproval.approval_required, "is_admin_role": adminapproval.is_admin_role,
          "waiver": adminapproval.waiver,
          "get_settings": lambda: NS(seyalrun_admin_approval=os.environ.get("SEYALRUN_ADMIN_APPROVAL"))}
    exec(compile(ast.Module(keep, []), "_approval", "exec"), ns)
    return {k: ns[k] for k in ("WAIVER_PARAM", "PRODUCTION", "TEMPLATE", "caller_waiver")}


class HTTPError(Exception):
    def __init__(self, status_code, detail=""):
        super().__init__(detail)
        self.status_code = status_code


def world(monkeypatch, run, tmpl=None, production=(), role=None):
    tree = ast.parse((AUTO / "api/job_runs.py").read_text())
    keep = []
    for n in tree.body:
        if isinstance(n, ast.AsyncFunctionDef) and n.name in ("rerun", "cancel_run"):
            n.decorator_list, n.args.defaults, n.returns = [], [], None
            for a in n.args.args:
                a.annotation = None
            keep.append(n)
        elif isinstance(n, ast.Assign) and n.targets[0].id == "_FINISHED":
            keep.append(n)
    w = NS(added=[], started=[], run=run)
    tmpl = tmpl or NS()
    t = NS(**{"id": "t", "name": "t", "enabled": True, "action_type": "bash_script", "requires_approval": False,
              "target_host_ids": [], "credential_id": None, "timeout_seconds": None, "retry_count": 0,
              "retry_delay_seconds": 0, "chain_steps": [], **vars(tmpl)})

    class Session:
        async def get(self, model, _id):
            return t if model == "tmpl" else run

        def add(self, row):
            w.added.append(row)

        async def commit(self):
            pass

    async def prod(ids):
        return bool(set(ids) & set(production))

    async def execute(run_id, *a, **k):
        w.started.append(run_id)

    class Redis:
        async def publish(self, *a):
            pass

    class Run:
        def __new__(cls, **k):
            return NS(**k)

    for name, mod in (("libs", types.ModuleType("libs")), ("libs.pluginbase", NS(RunRequest=lambda **k: NS(**k))),
                      ("app", NS(runner=NS(execute=execute)))):
        monkeypatch.setitem(sys.modules, name, mod)
    g = {"HTTPException": HTTPError, "ZAJobRun": Run, "ZAJobTemplate": "tmpl", "uuid": uuid, "asyncio": asyncio, "json": json,
         "datetime": datetime, "timezone": timezone, "get_redis": Redis, "any_production_hosts": prod,
         "template_code_params": lambda t: {}, "ACCOUNTABLE_PARAM": attr.ACCOUNTABLE_PARAM,
         "accountable_ids": attr.accountable_ids, "actor_user_id": attr.actor_user_id,
         # 3.0: patch/reboot runs and rollout hooks are refused separately
         "NO_RERUN_ACTIONS": frozenset({"package_ops", "host_reboot"}), "HOOK_TEMPLATE_PARAM": "_hook_template_updated_at",
         "APPROVED_BY_PARAM": "_approved_by", **_approval_helpers()}
    exec(compile(ast.Module(keep, []), "job_runs", "exec"), g)
    request = NS(app=NS(state=NS(executors={"bash_script": object(), "chain": object()})),
                 **({"headers": {"X-User-Real-Role": role}} if role else {}))

    async def rerun(uid="bob"):
        r = await g["rerun"]("r", request, uid, Session())
        await asyncio.sleep(0)
        return r

    async def cancel():
        return await g["cancel_run"]("r", "admin", Session())
    w.rerun, w.cancel = (lambda uid="bob": asyncio.run(rerun(uid))), (lambda: asyncio.run(cancel()))
    return w


def job(status, **params):
    return NS(id="r", job_template_id="t", triggered_by="user:alice", status=status, params=params, ended_at=None)


@pytest.mark.parametrize("status", ["pending_approval", "rejected", "pending", "running"])
def test_unfinished_or_rejected_run_cannot_be_rerun(monkeypatch, status):
    w = world(monkeypatch, job(status, _target_host_ids=["h"]))
    with pytest.raises(HTTPError) as e:
        w.rerun()
    assert e.value.status_code == 400 and not w.added and not w.started


@pytest.mark.parametrize("status", ["success", "failed", "error", "timeout", "cancelled"])
@pytest.mark.parametrize("why", ["production", "template"])
def test_rerun_waits_for_approval_like_a_new_run(monkeypatch, status, why):
    w = world(monkeypatch, job(status, _target_host_ids=["h"]), NS(requires_approval=why == "template"),
              production=["h"] if why == "production" else [])
    assert w.rerun()["status"] == "pending_approval"
    assert [r.status for r in w.added] == ["pending_approval"] and not w.started


def test_rerun_that_needs_no_approval_starts_at_once(monkeypatch):
    w = world(monkeypatch, job("failed", _target_host_ids=["h"]), production=["other"])
    r = w.rerun()
    assert r["status"] == "pending" and w.started == [r["run_id"]]


def test_everyone_accountable_stays_barred_from_approving(monkeypatch):
    w = world(monkeypatch, job("failed", _target_host_ids=["h"], _accountable_user_ids=["frank"]), production=["h"])
    w.rerun("bob")
    new = w.added[0]
    barred = new.params["_accountable_user_ids"]
    assert all(attr.is_requester(new.triggered_by, u, barred) for u in ("alice", "bob", "frank"))
    assert not attr.is_requester(new.triggered_by, "carol", barred)


def test_chain_is_rerun_with_its_current_steps(monkeypatch):
    w = world(monkeypatch, job("success", _chain_steps=[{"template_id": "old"}]),
              NS(action_type="chain", chain_steps=[{"template_id": "new"}]))
    w.rerun()
    assert w.added[0].params["_chain_steps"] == [{"template_id": "new"}]


@pytest.mark.parametrize("status,ok", [("rejected", False), ("timeout", False), ("success", False),
                                       ("pending_approval", True), ("running", True)])
def test_a_rejection_cannot_be_turned_into_a_cancellation(monkeypatch, status, ok):
    w = world(monkeypatch, job(status))
    if ok:
        w.cancel()
        assert w.run.status == "cancelled"
    else:
        with pytest.raises(HTTPError):
            w.cancel()
        assert w.run.status == status


# ── SEYALRUN_ADMIN_APPROVAL=off ──────────────────────────────────────────────

@pytest.mark.parametrize("why", ["production", "template"])
def test_switch_off_admin_rerun_starts_and_records_why(monkeypatch, why):
    monkeypatch.setenv("SEYALRUN_ADMIN_APPROVAL", "off")
    w = world(monkeypatch, job("failed", _target_host_ids=["h"]), NS(requires_approval=why == "template"),
              production=["h"] if why == "production" else [], role="admin")
    r = w.rerun()
    assert r["status"] == "pending" and w.started == [r["run_id"]]
    waived = w.added[0].params["_approval_waived"]
    assert waived["approval"] == adminapproval.NOT_REQUIRED
    assert waived["skipped"] == ["production host" if why == "production" else "template requires approval"]


@pytest.mark.parametrize("role", [None, "support", "user", "automation"])
def test_switch_off_non_admin_rerun_still_waits(monkeypatch, role):
    monkeypatch.setenv("SEYALRUN_ADMIN_APPROVAL", "off")
    w = world(monkeypatch, job("failed", _target_host_ids=["h"]), production=["h"], role=role)
    assert w.rerun()["status"] == "pending_approval" and not w.started
    assert "_approval_waived" not in w.added[0].params


@pytest.mark.parametrize("value", ["on", "ON", "yes", "", "0", "false"])
def test_switch_on_or_unknown_admin_rerun_still_waits(monkeypatch, value):
    monkeypatch.setenv("SEYALRUN_ADMIN_APPROVAL", value)
    w = world(monkeypatch, job("failed", _target_host_ids=["h"]), production=["h"], role="admin")
    assert w.rerun()["status"] == "pending_approval" and not w.started


def test_replayed_waiver_is_never_trusted(monkeypatch):
    """A run that skipped approval while the switch was off carries the marker; a
    re-run when it is on again must not inherit it."""
    w = world(monkeypatch, job("failed", _target_host_ids=["h"], _approval_waived={"approval": "x", "skipped": []}),
              production=["h"], role="admin")
    assert w.rerun()["status"] == "pending_approval"
    assert "_approval_waived" not in w.added[0].params
