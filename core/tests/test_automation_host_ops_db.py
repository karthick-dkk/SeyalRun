"""automation-service's side of patching on a REAL PostgreSQL: who may run what, and
when a package or reboot operation is started, followed or refused.

Opt-in, same variables as test_posture_m2_db.py (POSTURE_TEST_DB_HOST ...).
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

if not os.environ.get("POSTURE_TEST_DB_HOST"):
    pytest.skip("POSTURE_TEST_DB_HOST not set (opt-in DB test)", allow_module_level=True)
for _mod in ("fastapi", "sqlalchemy", "asyncpg", "httpx", "redis"):
    pytest.importorskip(_mod)

CORE = Path(__file__).resolve().parent.parent
SVC = CORE / "services/automation-service"
SECRET = "auto-test-secret-" + "x" * 32
DB = "automation_hostops_test"
H1, H2 = "00000001-0000-4000-8000-000000000000", "00000002-0000-4000-8000-000000000000"


@pytest.fixture(scope="module")
def svc():
    monkeypatch = pytest.MonkeyPatch()
    for k, v in {"DB_ENGINE": "postgres", "DB_SSLMODE": "disable", "DB_HOST": os.environ["POSTURE_TEST_DB_HOST"],
                 "DB_PORT": os.environ.get("POSTURE_TEST_DB_PORT", "5432"),
                 "DB_USER": os.environ.get("POSTURE_TEST_DB_USER", "postgres"),
                 "DB_PASSWORD": os.environ.get("POSTURE_TEST_DB_PASSWORD", "postgres"), "AUTOMATION_DB_NAME": DB,
                 "SERVICE_JWT_SECRET": SECRET, "LOG_PATH": os.devnull}.items():
        monkeypatch.setenv(k, v)
    saved = {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}
    for k in saved:
        del sys.modules[k]
    monkeypatch.syspath_prepend(str(SVC))
    try:
        yield SimpleNamespace(**{n: importlib.import_module(f"app.{m}") for n, m in {
            "internal": "api.internal", "runs": "api.job_runs", "templates": "api.job_templates", "posture": "_posture",
            "runner": "runner", "models": "models", "db": "database", "audit": "audit", "chain": "plugins.executors.chain",
            "approval": "_approval", "config": "config", "scheduler": "scheduler", "schedules": "api.schedules",
        }.items()})
    finally:
        for k in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
            del sys.modules[k]
        sys.modules.update(saved)
        monkeypatch.undo()


def run_case(svc, monkeypatch, body):
    """body(call, w): call() is one HTTP request; w.production decides the gate and
    w.executed lists the runs that really reached an executor."""
    import asyncpg
    import httpx
    from fastapi import FastAPI
    from libs.servicetoken import mint

    svc.runner.LIVE_RUNS.clear()
    w = SimpleNamespace(production={H2}, executed=[], block=asyncio.Event(), audit=[])

    async def prod(ids):
        return bool(set(ids) & w.production)

    async def execute(run_id, executor, request, settings=None, **kw):
        w.executed.append(run_id)
        await w.block.wait()

    async def log_action(**kw):
        w.audit.append(kw)

    async def main_():
        admin = await asyncpg.connect(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
                                      password=os.environ["DB_PASSWORD"], database="postgres")
        await admin.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{DB}"')
        await admin.close()
        async with svc.db.engine.begin() as conn:
            await conn.run_sync(svc.db.Base.metadata.create_all)
        for mod in (svc.internal, svc.runs, svc.templates):
            if hasattr(mod, "any_production_hosts"):
                monkeypatch.setattr(mod, "any_production_hosts", prod)
        monkeypatch.setattr(svc.runner, "_execute", execute)
        monkeypatch.setattr(svc.audit, "log_action", log_action)
        app = FastAPI()
        app.state.executors = {k: SimpleNamespace(name=k, validate=lambda p: None) for k in
                               ("package_ops", "host_reboot", "posture_facts", "bash_script", "chain")}
        for r in (svc.internal.router, svc.runs.router, svc.templates.router, svc.schedules.router):
            app.include_router(r, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            async def call(method, path, *, uid="alice", role="admin", code=None, **body):
                r = await c.request(method, "/api/v1" + path, json=body or None, headers={
                    "X-Service-Token": mint("posture-service", "automation-service", SECRET), "X-User-Id": uid,
                    "X-User-Name": uid, "X-User-Role": role, "X-User-Real-Role": role})
                assert code is None or r.status_code == code, (method, path, r.status_code, r.text[:400])
                return r.json() if r.content else None
            await body(call, w)
        w.block.set()
        await asyncio.sleep(0.05)

    async def whole():
        try:
            await main_()
        finally:
            await svc.db.engine.dispose()
    asyncio.run(whole())


def op(host=H1, token="op-1234567890", op="upgrade", **kw):
    return {"action": "package_ops", "op": op, "args": {"packages": [{"name": "openssl"}]}, "host_id": host,
            "token": token, "requested_by": "alice", **kw}


APPROVAL = {"reference": "rollout-1", "approved_by": "bob", "accountable_user_ids": ["alice"]}


async def get(svc, run_id):
    async with svc.db.SessionLocal() as s:
        return await s.get(svc.models.ZAJobRun, run_id)


async def template(call, **kw):
    return await call("POST", "/job-templates", code=201, project_id="p", name=kw.pop("name", "t"),
                      action_type=kw.pop("action_type", "bash_script"), **kw)


def test_live_operation_is_reused_and_blocks_others(svc, monkeypatch):
    async def body(call, w):
        a = await call("POST", "/internal/posture/host-runs", code=202, **op())
        b = await call("POST", "/internal/posture/host-runs", code=202, **op())
        assert b["run_id"] == a["run_id"] and b["reused"]
        await call("POST", "/internal/posture/host-runs", code=409, **op(token="op-other-00000"))
        await call("POST", "/internal/posture/host-runs", code=202, **op(token="op-plan-000000", op="plan"))
        await call("POST", "/internal/posture/host-runs", code=202, **op(host="00000003-0000-4000-8000-000000000000", token="op-else-000000"))
        assert (await call("GET", f"/internal/job-runs/{a['run_id']}"))["live"]
        await asyncio.sleep(0.02)
        assert len(w.executed) == 3
    run_case(svc, monkeypatch, body)


def test_run_left_by_a_restart_is_continued_and_blocks_nothing(svc, monkeypatch):
    async def body(call, w):
        a = await call("POST", "/internal/posture/host-runs", code=202, **op())
        svc.runner.LIVE_RUNS.clear()                    # the process went away
        b = await call("POST", "/internal/posture/host-runs", code=202, **op())
        assert b["run_id"] != a["run_id"] and (await get(svc, a["run_id"])).status == "cancelled"
        svc.runner.LIVE_RUNS.clear()
        await call("POST", "/internal/posture/host-runs", code=202, **op(token="op-other-00000"))
        async with svc.db.SessionLocal() as s:
            await svc.posture.reconcile_interrupted(s)
        assert (await get(svc, b["run_id"])).status == "error"
    run_case(svc, monkeypatch, body)


def test_waiting_request_does_not_block_and_is_superseded_by_an_approved_rollout(svc, monkeypatch):
    async def body(call, w):
        a = await call("POST", "/internal/posture/host-runs", code=202, **op(host=H2))
        assert a["status"] == "pending_approval"
        b = await call("POST", "/internal/posture/host-runs", code=202, **op(host=H2, token="op-rollout-000", approval=APPROVAL))
        assert b["status"] == "pending" and (await get(svc, a["run_id"])).status == "cancelled"
        await call("POST", f"/job-runs/{a['run_id']}/approve", uid="bob", code=400)
        await asyncio.sleep(0.02)
        assert w.executed == [b["run_id"]]
    run_case(svc, monkeypatch, body)


def test_approval_is_refused_while_another_operation_changes_the_host(svc, monkeypatch):
    async def body(call, w):
        a = await call("POST", "/internal/posture/host-runs", code=202, **op(host=H2))
        w.production = set()
        await call("POST", "/internal/posture/host-runs", code=202, **op(host=H2, token="op-second-0000"))
        await call("POST", f"/job-runs/{a['run_id']}/approve", uid="bob", code=409)
        assert (await get(svc, a["run_id"])).status == "pending_approval" and not w.audit
    run_case(svc, monkeypatch, body)


def test_approved_operation_is_live_and_never_gated_again(svc, monkeypatch):
    async def body(call, w):
        a = await call("POST", "/internal/posture/host-runs", code=202, **op(host=H2, accountable_user_ids=["mirror-editor"]))
        await call("POST", f"/job-runs/{a['run_id']}/approve", uid="alice", code=403)
        await call("POST", f"/job-runs/{a['run_id']}/approve", uid="mirror-editor", code=403)
        await call("POST", f"/job-runs/{a['run_id']}/approve", uid="bob", code=202)
        assert (await call("GET", f"/internal/job-runs/{a['run_id']}"))["live"]
        assert (await call("POST", "/internal/posture/host-runs", code=202, **op(host=H2)))["run_id"] == a["run_id"]
        svc.runner.LIVE_RUNS.clear()
        async with svc.db.SessionLocal() as s:
            assert await svc.posture.reconcile_interrupted(s) == 1
        b = await call("POST", "/internal/posture/host-runs", code=202, **op(host=H2))
        assert b["run_id"] != a["run_id"] and b["status"] == "pending"
        # the approval is for that operation only: not other packages, not another host
        svc.runner.LIVE_RUNS.clear()
        async with svc.db.SessionLocal() as s:
            await svc.posture.reconcile_interrupted(s)
        changed = op(host=H2)
        changed["args"] = {"packages": [{"name": "sudo"}]}
        assert (await call("POST", "/internal/posture/host-runs", code=202, **changed))["status"] == "pending_approval"
        w.production.add(H1)
        assert (await call("POST", "/internal/posture/host-runs", code=202, **op(host=H1)))["status"] == "pending_approval"
        assert (await call("POST", "/internal/posture/host-runs", code=202,
                           **op(host=H2, token="op-new-0000000")))["status"] == "pending_approval"
    run_case(svc, monkeypatch, body)


def test_facts_runs_reuse_only_what_is_really_running(svc, monkeypatch):
    async def body(call, w):
        async def ask(host=H1, **kw):
            return (await call("POST", "/internal/posture/facts-runs", code=202, host_ids=[host], requested_by="alice", **kw))["runs"][0]
        a = await ask()
        assert (await ask())["run_id"] == a["run_id"]
        svc.runner.LIVE_RUNS.clear()
        assert (await ask())["run_id"] != a["run_id"]
        p = await ask(H2)
        assert p["status"] == "pending_approval" and (await ask(H2))["run_id"] == p["run_id"]
        q = await ask(H2, approval=APPROVAL)
        assert q["status"] == "pending" and (await get(svc, p["run_id"])).status == "cancelled"
    run_case(svc, monkeypatch, body)


def test_patching_templates_run_from_posture_only(svc, monkeypatch):
    async def body(call, w):
        await call("POST", "/internal/posture/host-runs", code=202, **op())
        svc.runner.LIVE_RUNS.clear()
        async with svc.db.SessionLocal() as s:
            sysid = (await svc.posture.ensure_host_op_template(s, "package_ops")).id
        evil = {"_host_op": {"op": "upgrade", "args": {"packages": [{"name": "sudo"}]}}, "_op_token": "op-evil-000000"}
        for action in ("package_ops", "host_reboot"):
            await call("POST", "/job-templates", code=400, project_id="p", name="x", action_type=action, default_params=evil)
        await call("POST", "/job-templates", code=400, project_id="p", name="x", action_type="bash_script", default_params=evil)
        t = await template(call)
        await call("PUT", f"/job-templates/{t['id']}", code=400, action_type="package_ops")
        await call("PUT", f"/job-templates/{t['id']}", code=400, default_params=evil)
        await call("PUT", f"/job-templates/{sysid}", code=400, default_params=evil)
        await call("POST", f"/job-templates/{sysid}/run", code=400, target_host_ids=[H1])
        await call("POST", "/internal/job-runs", code=400, job_template_id=sysid, triggered_by="zabbix", target_host_ids=[H1])
        await asyncio.sleep(0.02)
        assert len(w.executed) == 1
        # still tunable by an admin, and still the built-in template
        await call("PUT", f"/job-templates/{sysid}", code=200, default_params={"use_sudo": True}, action_type="bash_script")
        async with svc.db.SessionLocal() as s:
            assert (await s.get(svc.models.ZAJobTemplate, sysid)).action_type == "package_ops"
    run_case(svc, monkeypatch, body)


def test_everyone_who_edited_a_hook_is_named(svc, monkeypatch):
    async def body(call, w):
        t = await template(call, script_content="true")
        await call("PUT", f"/job-templates/{t['id']}", uid="carol", code=200, script_content="echo hi")
        await call("PUT", f"/job-templates/{t['id']}", uid="dave", code=200, description="d")
        got = await call("GET", f"/internal/posture/hook-templates/{t['id']}", code=200)
        assert set(got["editor_ids"]) == {"alice", "carol", "dave"}
    run_case(svc, monkeypatch, body)


def test_reconcile_works_before_the_new_migration_is_applied(svc, monkeypatch):
    """Deploys start the service first and migrate afterwards."""
    async def body(call, w):
        from sqlalchemy import text

        a = await call("POST", "/internal/posture/host-runs", code=202, **op())
        svc.runner.LIVE_RUNS.clear()
        async with svc.db.SessionLocal() as s:
            await s.execute(text("ALTER TABLE za_job_templates DROP COLUMN editor_ids"))
            await s.commit()
        async with svc.db.SessionLocal() as s:
            assert await svc.posture.reconcile_interrupted(s) == 1
        assert (await get(svc, a["run_id"])).status == "error"
    run_case(svc, monkeypatch, body)


def test_per_run_approval_of_a_hook_is_barred_to_its_editors(svc, monkeypatch):
    async def body(call, w):
        t = await template(call, script_content="true")
        await call("PUT", f"/job-templates/{t['id']}", uid="carol", code=200, description="d")
        got = await call("GET", f"/internal/posture/hook-templates/{t['id']}")
        r = await call("POST", "/internal/posture/hook-runs", code=202, job_template_id=t["id"], expect_updated_at=got["updated_at"],
                       host_id=H2, requested_by="erin", accountable_user_ids=["frank"])
        assert r["status"] == "pending_approval"
        for uid in ("erin", "frank", "alice", "carol"):
            await call("POST", f"/job-runs/{r['run_id']}/approve", uid=uid, code=403)
        await call("POST", f"/job-runs/{r['run_id']}/approve", uid="bob", code=202)
    run_case(svc, monkeypatch, body)


def test_hook_edited_while_it_waits_cannot_be_approved(svc, monkeypatch):
    """The editor is not on the run's list: the list was made before the edit."""
    async def body(call, w):
        t = await template(call, script_content="true")
        got = await call("GET", f"/internal/posture/hook-templates/{t['id']}")
        r = await call("POST", "/internal/posture/hook-runs", code=202, job_template_id=t["id"], expect_updated_at=got["updated_at"],
                       host_id=H2, requested_by="erin")
        await call("PUT", f"/job-templates/{t['id']}", uid="mallory", code=200, script_content="evil")
        for uid in ("mallory", "bob"):
            await call("POST", f"/job-runs/{r['run_id']}/approve", uid=uid, code=409)
        await asyncio.sleep(0.02)
        assert not w.executed and not any(a["action"] == "job.approve" for a in w.audit)
        assert (await get(svc, r["run_id"])).status == "pending_approval"
        await call("POST", f"/job-runs/{r['run_id']}/reject", uid="bob", code=200, reason="changed")
    run_case(svc, monkeypatch, body)


def test_rerun_never_skips_an_approval(svc, monkeypatch):
    async def body(call, w):
        t = await template(call, script_content="true", target_host_ids=[H2])
        async def ended(status, **params):
            rid = str(uuid.uuid4())
            async with svc.db.SessionLocal() as s:
                s.add(svc.models.ZAJobRun(id=rid, job_template_id=t["id"], triggered_by="user:alice", status=status,
                                          params={"_target_host_ids": [H2], **params}, output_lines=[]))
                await s.commit()
            return rid
        for status in ("pending_approval", "rejected", "pending", "running"):
            await call("POST", f"/job-runs/{await ended(status)}/rerun", code=400)
        # cancelled before anyone approved it, and one that was approved once: both wait again
        for rid in (await ended("cancelled"), await ended("success", _approved_by="bob")):
            r = await call("POST", f"/job-runs/{rid}/rerun", uid="alice", code=202)
            run = await get(svc, r["run_id"])
            assert run.status == r["status"] == "pending_approval" and "_approved_by" not in run.params
            await call("POST", f"/job-runs/{r['run_id']}/approve", uid="alice", code=403)
        await asyncio.sleep(0.02)
        assert not w.executed
        await call("POST", f"/job-runs/{r['run_id']}/approve", uid="bob", code=202)
        w.production = set()                    # not gated: runs at once, as before
        r = await call("POST", f"/job-runs/{await ended('failed')}/rerun", code=202)
        await asyncio.sleep(0.02)
        assert r["status"] == "pending" and r["run_id"] in w.executed
        await call("PUT", f"/job-templates/{t['id']}", code=200, requires_approval=True)
        assert (await call("POST", f"/job-runs/{await ended('failed')}/rerun", code=202))["status"] == "pending_approval"
    run_case(svc, monkeypatch, body)


def test_hook_run_cannot_be_rerun(svc, monkeypatch):
    async def body(call, w):
        t = await template(call, script_content="true")
        got = await call("GET", f"/internal/posture/hook-templates/{t['id']}")
        r = await call("POST", "/internal/posture/hook-runs", code=202, job_template_id=t["id"], expect_updated_at=got["updated_at"],
                       host_id=H1, requested_by="erin")
        async with svc.db.SessionLocal() as s:
            (await s.get(svc.models.ZAJobRun, r["run_id"])).status = "success"
            await s.commit()
        await call("POST", f"/job-runs/{r['run_id']}/rerun", code=400)
    run_case(svc, monkeypatch, body)


def test_original_requester_cannot_approve_a_rerun(svc, monkeypatch):
    async def body(call, w):
        t = await template(call, script_content="true", target_host_ids=[H2])
        rid = str(uuid.uuid4())
        async with svc.db.SessionLocal() as s:
            s.add(svc.models.ZAJobRun(id=rid, job_template_id=t["id"], triggered_by="user:erin", status="failed", output_lines=[],
                                      params={"_target_host_ids": [H2], "_accountable_user_ids": ["frank"]}))
            await s.commit()
        for uid in ("gina", "hank"):            # a re-run of a re-run keeps everyone
            rid = (await call("POST", f"/job-runs/{rid}/rerun", uid=uid, code=202))["run_id"]
            async with svc.db.SessionLocal() as s:
                (await s.get(svc.models.ZAJobRun, rid)).status = "failed" if uid == "gina" else "pending_approval"
                await s.commit()
        for uid in ("erin", "frank", "gina", "hank"):
            await call("POST", f"/job-runs/{rid}/approve", uid=uid, code=403)
        await call("POST", f"/job-runs/{rid}/approve", uid="bob", code=202)
    run_case(svc, monkeypatch, body)


def test_rejected_run_stays_rejected(svc, monkeypatch):
    async def body(call, w):
        class Redis:
            async def publish(self, *a):
                return 0
        monkeypatch.setattr(svc.runs, "get_redis", Redis)
        t = await template(call, script_content="true", target_host_ids=[H2])
        r = await call("POST", f"/job-templates/{t['id']}/run", code=202, target_host_ids=[H2])
        await call("POST", f"/job-runs/{r['run_id']}/reject", uid="bob", code=200, reason="no")
        await call("DELETE", f"/job-runs/{r['run_id']}", code=400)
        assert (await get(svc, r["run_id"])).status == "rejected"
        await call("POST", f"/job-runs/{r['run_id']}/rerun", code=400)
        r = await call("POST", f"/job-templates/{t['id']}/run", code=202, target_host_ids=[H2])
        await call("DELETE", f"/job-runs/{r['run_id']}", code=204)
    run_case(svc, monkeypatch, body)


def test_chain_rerun_uses_the_steps_the_template_has_now(svc, monkeypatch):
    async def body(call, w):
        a, b = await template(call, name="a"), await template(call, name="b")
        c = await template(call, name="c", action_type="chain", chain_steps=[{"template_id": a["id"]}, {"template_id": b["id"]}])
        rid = str(uuid.uuid4())
        async with svc.db.SessionLocal() as s:
            s.add(svc.models.ZAJobRun(id=rid, job_template_id=c["id"], triggered_by="user:erin", status="success", output_lines=[],
                                      params={"_chain_steps": [{"template_id": a["id"]}, {"template_id": b["id"]}]}))
            await s.commit()
        await call("PUT", f"/job-templates/{c['id']}", code=200, chain_steps=[{"template_id": b["id"]}])
        r = await call("POST", f"/job-runs/{rid}/rerun", code=202)
        assert (await get(svc, r["run_id"])).params["_chain_steps"] == [{"template_id": b["id"]}]
    run_case(svc, monkeypatch, body)


def test_run_whose_template_changed_while_waiting_cannot_be_approved(svc, monkeypatch):
    async def body(call, w):
        async def waiting(t, **kw):
            return (await call("POST", f"/job-templates/{t['id']}/run", code=202, target_host_ids=[H2], **kw))["run_id"]
        a = await template(call, name="a", script_content="true")
        t = await template(call, name="t", script_content="true")
        rid = await waiting(t)
        await call("PUT", f"/job-templates/{a['id']}", code=200, script_content="other")      # not this one
        await call("PUT", f"/job-templates/{t['id']}", uid="bob", code=200, script_content="rm")
        await call("POST", f"/job-runs/{rid}/approve", uid="carol", code=409)
        assert (await get(svc, rid)).status == "pending_approval"
        await call("POST", f"/job-runs/{rid}/reject", uid="carol", code=200, reason="changed")
        rid = await waiting(t)
        await call("POST", f"/job-runs/{rid}/approve", uid="carol", code=202)
        # the webhook path, and a run from before versions were recorded
        r = await call("POST", "/internal/job-runs", job_template_id=a["id"], triggered_by="zabbix_event:1", target_host_ids=[H2], params={})
        await call("PUT", f"/job-templates/{a['id']}", code=200, approver_role="admin")
        await call("POST", f"/job-runs/{r['run_id']}/approve", uid="carol", code=409)
        async with svc.db.SessionLocal() as s:
            run = await s.get(svc.models.ZAJobRun, r["run_id"])
            run.params = {k: v for k, v in run.params.items() if k != "_template_version"}
            await s.commit()
        assert "before an upgrade" in (await call("POST", f"/job-runs/{r['run_id']}/approve", uid="carol", code=409))["detail"]
        # a chain is also what its steps are
        c = await template(call, name="c", action_type="chain", requires_approval=True, chain_steps=[{"template_id": t["id"]}])
        rid = await waiting(c)
        await call("PUT", f"/job-templates/{t['id']}", uid="bob", code=200, script_content="rm2")
        await call("POST", f"/job-runs/{rid}/approve", uid="carol", code=409)
        await call("POST", f"/job-runs/{rid}/reject", uid="carol", code=200, reason="changed")
        rid = await waiting(c)
        await call("POST", f"/job-runs/{rid}/approve", uid="carol", code=202)
    run_case(svc, monkeypatch, body)


@pytest.mark.parametrize("when", ["before", "during"])
def test_chain_refuses_a_step_that_requires_approval(svc, monkeypatch, when):
    async def body(call, w):
        import app._production_gate as gate
        import libs.pluginbase as pb
        async def none(ids):
            return False
        monkeypatch.setattr(gate, "any_production_hosts", none)
        a = await template(call, name="a", script_content="true")
        b = await template(call, name="b", script_content="true", requires_approval=when == "before")
        ran, lines = [], []
        async def execute(run_id, executor, request, **kw):
            ran.append(run_id)
            async with svc.db.SessionLocal() as s:
                (await s.get(svc.models.ZAJobRun, run_id)).status = "success"
                (await s.get(svc.models.ZAJobTemplate, b["id"])).requires_approval = True
                await s.commit()
        monkeypatch.setattr(svc.runner, "execute", execute)
        monkeypatch.setattr(pb, "discover_plugins", lambda *x: {"bash_script": SimpleNamespace(name="bash_script")})
        async def line(text, *x):
            lines.append(text)
        res = await svc.chain.ChainExecutor().execute(pb.RunRequest(
            action_type="chain", target_host_ids=[], credential_id=None, triggered_by="user:alice",
            params={"_run_id": "p", "_chain_steps": [{"template_id": a["id"], "continue_on_failure": True}, {"template_id": b["id"]}]}), line)
        assert not res.ok and "requires approval" in res.output, lines
        assert len(ran) == (0 if when == "before" else 1), lines
    run_case(svc, monkeypatch, body)


# ── SEYALRUN_ADMIN_APPROVAL=off ──────────────────────────────────────────────
# Admins (alice, bob, sam) need no second admin; anyone else (sue: support, ursula:
# user, an unknown id, or anyone while identity-service cannot answer) still does.

NOT_REQUIRED = "not required (SEYALRUN_ADMIN_APPROVAL=off)"
# id: (effective roles, is_active). dora is an admin who has been deactivated.
USERS = {"alice": (["admin"], True), "bob": (["admin"], True), "sam": (["support", "superadmin"], True),
         "sue": (["support"], True), "ursula": (["user"], True), "dora": (["admin"], False)}


def identity(down=False):
    """identity-service's GET /internal/users/{id}/roles, as it answers now."""
    import httpx

    def handler(req):
        if down:
            raise httpx.ConnectError("identity-service down")
        assert req.headers.get("X-Service-Token")
        uid = req.url.path.split("/")[-2]
        if uid not in USERS:
            return httpx.Response(200, json={"roles": [], "known": False, "is_active": False})
        roles, active = USERS[uid]
        return httpx.Response(200, json={"roles": roles, "known": True, "is_active": active})
    return httpx.MockTransport(handler)


def switch(svc, monkeypatch, value, *, down=False):
    """Set the service's own setting (as .env would); the real role lookup talks to a
    simulated identity-service."""
    monkeypatch.setattr(svc.config.get_settings(), "seyalrun_admin_approval", value)
    real = svc.approval.fetch_roles

    async def fetch_roles(ids, transport=None):
        return await real(ids, transport=identity(down))
    monkeypatch.setattr(svc.approval, "fetch_roles", fetch_roles)


def waived(run, *skipped):
    return run.params.get("_approval_waived") == {"approval": NOT_REQUIRED, "skipped": list(skipped)}


def test_switch_off_admin_package_and_reboot_ops_on_production_run_at_once(svc, monkeypatch):
    switch(svc, monkeypatch, "off")

    async def body(call, w):
        w.production = {H1, H2}
        up = await call("POST", "/internal/posture/host-runs", code=202, **op(host=H2))
        rb = await call("POST", "/internal/posture/host-runs", code=202, **{
            **op(host=H1, token="op-reboot-0000", op="reboot"), "action": "host_reboot", "args": {}})
        for r in (up, rb):
            assert r["status"] == "pending"
            assert waived(await get(svc, r["run_id"]), "production host")
        await asyncio.sleep(0.02)
        assert set(w.executed) == {up["run_id"], rb["run_id"]}
        # A rollout a second admin approved is still recorded as that approval.
        w.block.set()
        await asyncio.sleep(0.02)
        svc.runner.LIVE_RUNS.clear()
        async with svc.db.SessionLocal() as s:
            await svc.posture.reconcile_interrupted(s)
        r = await call("POST", "/internal/posture/host-runs", code=202, **op(host=H2, token="op-rollout-000", approval=APPROVAL))
        assert r["status"] == "pending" and "_approval_waived" not in (await get(svc, r["run_id"])).params
        assert any(a["action"] == "job.approve" and a["user_id"] == "bob" for a in w.audit)
    run_case(svc, monkeypatch, body)


@pytest.mark.parametrize("who,value,down", [("sue", "off", False), ("ursula", "off", False), ("nobody", "off", False),
                                            ("dora", "off", False),
                                            ("alice", "off", True), ("alice", "on", False), ("alice", "maybe", False),
                                            ("alice", "", False)])
def test_switch_host_ops_still_gated_unless_off_and_admin(svc, monkeypatch, who, value, down):
    switch(svc, monkeypatch, value, down=down)

    async def body(call, w):
        r = await call("POST", "/internal/posture/host-runs", code=202, **{**op(host=H2), "requested_by": who})
        assert r["status"] == "pending_approval"
        assert "_approval_waived" not in (await get(svc, r["run_id"])).params
        f = (await call("POST", "/internal/posture/facts-runs", code=202, host_ids=[H2], requested_by=who))["runs"][0]
        assert f["status"] == "pending_approval"
        await asyncio.sleep(0.02)
        assert not w.executed
    run_case(svc, monkeypatch, body)


def test_switch_off_admin_facts_run_on_production_and_older_wait_is_superseded(svc, monkeypatch):
    async def body(call, w):
        old = (await call("POST", "/internal/posture/facts-runs", code=202, host_ids=[H2], requested_by="alice"))["runs"][0]
        assert old["status"] == "pending_approval"
        switch(svc, monkeypatch, "off")
        new = (await call("POST", "/internal/posture/facts-runs", code=202, host_ids=[H1, H2], requested_by="alice"))["runs"]
        assert [r["status"] for r in new] == ["pending", "pending"] and not any(r["reused"] for r in new)
        assert (await get(svc, old["run_id"])).status == "cancelled"
        assert waived(await get(svc, new[1]["run_id"]), "production host")
        assert "_approval_waived" not in (await get(svc, new[0]["run_id"])).params, "nothing was skipped on H1"
    run_case(svc, monkeypatch, body)


@pytest.mark.parametrize("value", ["off", "on"])
def test_switch_hook_runs(svc, monkeypatch, value):
    switch(svc, monkeypatch, value)

    async def body(call, w):
        t = await template(call, script_content="true", requires_approval=True)
        got = await call("GET", f"/internal/posture/hook-templates/{t['id']}")
        hook = {"job_template_id": t["id"], "expect_updated_at": got["updated_at"], "requested_by": "alice"}
        prod = await call("POST", "/internal/posture/hook-runs", code=202, host_id=H2, **hook)
        plain = await call("POST", "/internal/posture/hook-runs", code=202, host_id=H1, **hook)
        if value == "off":
            assert prod["status"] == plain["status"] == "pending"
            assert waived(await get(svc, prod["run_id"]), "production host", "template requires approval")
            assert waived(await get(svc, plain["run_id"]), "template requires approval")
        else:
            # A hook that needs its own approval only reaches here from a rollout planned
            # while the switch was off: with it on again, the hook waits.
            assert prod["status"] == plain["status"] == "pending_approval"
    run_case(svc, monkeypatch, body)


def test_switch_off_manual_run_admin_bypasses_non_admin_waits(svc, monkeypatch):
    switch(svc, monkeypatch, "off")

    async def body(call, w):
        t = await template(call, script_content="true", requires_approval=True)
        r = await call("POST", f"/job-templates/{t['id']}/run", code=202, target_host_ids=[H2])
        assert r.get("status") != "pending_approval"
        assert waived(await get(svc, r["run_id"]), "template requires approval", "production host")
        await asyncio.sleep(0.02)
        assert r["run_id"] in w.executed
        u = await template(call, name="u", script_content="true", requires_approval=True)
        for who, role in (("sue", "support"), ("ursula", "user")):
            r = await call("POST", f"/job-templates/{u['id']}/run", uid=who, role=role, code=202, target_host_ids=[H2])
            assert r["status"] == "pending_approval"
            # already pending: approved the usual way, by another admin
            await call("POST", f"/job-runs/{r['run_id']}/approve", uid=who, role="admin", code=403)
            await call("POST", f"/job-runs/{r['run_id']}/approve", uid="bob", code=202)
            w.block.set()
            await asyncio.sleep(0.02)
            svc.runner.LIVE_RUNS.clear()
            async with svc.db.SessionLocal() as s:
                (await s.get(svc.models.ZAJobRun, r["run_id"])).status = "success"
                await s.commit()
        # the waiver is in the job.start audit row
        details = svc.runner.start_details(SimpleNamespace(
            action_type="bash_script", target_host_ids=[H2], credential_id=None, triggered_by="user:alice",
            params=(await get(svc, w.executed[0])).params), SimpleNamespace(name="bash_script"), "t")
        assert details["approval"] == NOT_REQUIRED and details["approval_skipped"] == ["template requires approval", "production host"]
    run_case(svc, monkeypatch, body)


@pytest.mark.parametrize("value,who,role,gated", [("off", "alice", "admin", False), ("off", "sam", "superadmin", False),
                                                  ("off", "sue", "support", True), ("on", "alice", "admin", True),
                                                  ("nonsense", "alice", "admin", True)])
def test_switch_rerun(svc, monkeypatch, value, who, role, gated):
    switch(svc, monkeypatch, value)

    async def body(call, w):
        t = await template(call, script_content="true", target_host_ids=[H2])
        rid = str(uuid.uuid4())
        async with svc.db.SessionLocal() as s:
            s.add(svc.models.ZAJobRun(id=rid, job_template_id=t["id"], triggered_by="user:erin", status="failed",
                                      params={"_target_host_ids": [H2]}, output_lines=[]))
            await s.commit()
        r = await call("POST", f"/job-runs/{rid}/rerun", uid=who, role=role, code=202)
        assert r["status"] == ("pending_approval" if gated else "pending")
        assert waived(await get(svc, r["run_id"]), "production host") is not gated
    run_case(svc, monkeypatch, body)


@pytest.mark.parametrize("triggered_by,accountable,gated", [
    ("zabbix_event:1", ["alice", "bob"], False),          # a binding only admins wrote
    ("zabbix_manual:9", ["sam"], False),
    ("manual_trigger:b1:alice", ["bob"], False),          # an admin fired an admin's binding
    ("manual_trigger:b1:sue", ["bob"], True),             # support fired it
    ("zabbix_event:1", ["alice", "sue"], True),           # support edited the binding
    ("zabbix_event:1", [], True),                         # nobody on record
    ("system:rotation_due_sweep", [], True),
    ("user:alice", [], False),                            # rotate-now by an admin
    ("zabbix_event:1", ["dora"], True),                   # its author has been deactivated
])
def test_switch_off_webhook_and_internal_runs(svc, monkeypatch, triggered_by, accountable, gated):
    switch(svc, monkeypatch, "off")

    async def body(call, w):
        t = await template(call, script_content="true")
        r = await call("POST", "/internal/job-runs", code=202, job_template_id=t["id"], triggered_by=triggered_by,
                       target_host_ids=[H2], params={}, accountable_user_ids=accountable)
        run = await get(svc, r["run_id"])
        assert run.status == ("pending_approval" if gated else "pending")
        assert waived(run, "production host") is not gated
    run_case(svc, monkeypatch, body)


@pytest.mark.parametrize("value,editors,gated", [("off", ["alice", "bob"], False), ("off", ["alice", "sue"], True),
                                                 ("off", ["alice", "dora"], True),
                                                 ("off", [], True), ("on", ["alice"], True), ("bogus", ["alice"], True)])
def test_switch_scheduler(svc, monkeypatch, value, editors, gated):
    switch(svc, monkeypatch, value)

    async def body(call, w):
        from datetime import datetime, timedelta, timezone

        async def prod(ids):
            return bool(set(ids) & w.production)
        monkeypatch.setattr(svc.scheduler, "any_production_hosts", prod)
        t = await template(call, script_content="true", target_host_ids=[H2])
        async with svc.db.SessionLocal() as s:
            s.add(svc.models.ZASchedule(id="s1", job_template_id=t["id"], name="s", cron_expression="* * * * *",
                                        params_override={}, enabled=True, created_by=(editors or [None])[0],
                                        editor_ids=editors or None,
                                        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
            await s.commit()
        await svc.scheduler._tick({"bash_script": SimpleNamespace(name="bash_script")}, svc.config.get_settings())
        await asyncio.sleep(0.02)
        async with svc.db.SessionLocal() as s:
            from sqlalchemy import select
            run = (await s.execute(select(svc.models.ZAJobRun).where(svc.models.ZAJobRun.triggered_by == "schedule:s1"))).scalar_one()
        assert run.status == ("pending_approval" if gated else "pending")
        assert waived(run, "production host") is not gated
        assert (run.id in w.executed) is not gated
    run_case(svc, monkeypatch, body)


@pytest.mark.parametrize("who,value,ok", [("alice", "off", True), ("sue", "off", False), ("alice", "on", False)])
def test_switch_chain_runs_steps_it_could_not_carry_approval_for(svc, monkeypatch, who, value, ok):
    switch(svc, monkeypatch, value)

    async def body(call, w):
        import app._production_gate as gate
        import libs.pluginbase as pb

        async def prod(ids):
            return bool(set(ids) & w.production)
        monkeypatch.setattr(gate, "any_production_hosts", prod)
        a = await template(call, name="a", script_content="true", target_host_ids=[H2])        # production
        b = await template(call, name="b", script_content="true", requires_approval=True)
        ran, lines = [], []

        async def execute(run_id, executor, request, **kw):
            ran.append((run_id, request.params.get("_approval_waived")))
            async with svc.db.SessionLocal() as s:
                (await s.get(svc.models.ZAJobRun, run_id)).status = "success"
                await s.commit()
        monkeypatch.setattr(svc.runner, "execute", execute)
        monkeypatch.setattr(pb, "discover_plugins", lambda *x: {"bash_script": SimpleNamespace(name="bash_script")})

        async def line(text, *x):
            lines.append(text)
        res = await svc.chain.ChainExecutor().execute(pb.RunRequest(
            action_type="chain", target_host_ids=[], credential_id=None, triggered_by=f"user:{who}",
            params={"_run_id": "p", "_chain_steps": [{"template_id": a["id"]}, {"template_id": b["id"]}]}), line)
        if ok:
            assert res.ok, lines
            assert [w_["skipped"] for _, w_ in ran] == [["production host"], ["template requires approval"]]
        else:
            assert not res.ok and "production" in res.output and not ran, lines
    run_case(svc, monkeypatch, body)


def test_identity_role_lookup_is_fail_closed(svc, monkeypatch):
    """The real fetch over HTTP: unknown users, refused lookups and errors never read as admin."""
    import httpx

    def handler(req):
        uid = req.url.path.split("/")[-2]
        assert req.headers.get("X-Service-Token")
        if uid == "boom":
            return httpx.Response(500)
        return httpx.Response(200, json={
            "alice": {"roles": ["admin"], "known": True, "is_active": True},
            "sue": {"roles": ["support"], "known": True, "is_active": True},
            "dora": {"roles": ["admin"], "known": True, "is_active": False},        # deactivated
            "old": {"roles": ["admin"], "known": True},                             # does not say: not an admin
            "odd": {"roles": ["admin"], "known": "yes", "is_active": 1},            # ambiguous: not an admin
        }.get(uid, {"roles": [], "known": False, "is_active": False}))

    async def main_():
        got = await svc.approval.fetch_roles(["alice", "sue", "ghost", "boom", "dora", "old", "odd"],
                                             transport=httpx.MockTransport(handler))
        assert got == {"alice": ["admin"], "sue": ["support"], "ghost": None, "boom": None, "dora": None, "old": None,
                       "odd": None}
    asyncio.run(main_())

    async def real(ids, transport=None):
        return await orig(ids, transport=httpx.MockTransport(handler))
    orig = svc.approval.fetch_roles
    monkeypatch.setattr(svc.approval, "fetch_roles", real)

    async def check():
        assert await svc.approval.users_are_admins(["alice"])
        assert not await svc.approval.users_are_admins(["alice", "sue"])
        assert not await svc.approval.users_are_admins(["alice", "ghost"])
        assert not await svc.approval.users_are_admins(["boom"])
        assert not await svc.approval.users_are_admins(["dora"])
        assert not await svc.approval.users_are_admins(["alice", "dora"])
        assert not await svc.approval.users_are_admins(["old"])
        assert not await svc.approval.users_are_admins([])
        assert not await svc.approval.users_are_admins(["", None])
    asyncio.run(check())


# ── a forged "approval not required" marker never survives into a run ────────
# The marker is trusted by the job.start audit. Stored data (a template's
# default_params, a schedule's params_override, rows from before the API checks)
# must never be able to supply it.

FORGED = {"_approval_waived": {"approval": NOT_REQUIRED, "skipped": ["production host"]}}


async def forge_template(svc, tid):
    """Written straight to the row, as a pre-check row or a database edit would be."""
    async with svc.db.SessionLocal() as s:
        t = await s.get(svc.models.ZAJobTemplate, tid)
        t.default_params = {**(t.default_params or {}), **FORGED, "_accountable_user_ids": []}
        await s.commit()


def no_marker(svc, params):
    d = svc.runner.start_details(SimpleNamespace(action_type="bash_script", target_host_ids=[], credential_id=None,
                                                 triggered_by="x", params=params), None, "t")
    return "_approval_waived" not in params and "approval" not in d


def test_schedule_and_template_api_refuse_internal_params(svc, monkeypatch):
    async def body(call, w):
        t = await template(call, script_content="true")
        await call("POST", "/job-templates", code=400, project_id="p", name="x", action_type="bash_script", default_params=FORGED)
        await call("POST", "/schedules", code=400, job_template_id=t["id"], name="s", cron_expression="* * * * *",
                   params_override=FORGED)
        s = await call("POST", "/schedules", code=201, job_template_id=t["id"], name="s", cron_expression="* * * * *",
                       params_override={"x": 1})
        await call("PUT", f"/schedules/{s['id']}", code=400, params_override={"_accountable_user_ids": []})
        await call("PUT", f"/schedules/{s['id']}", code=200, params_override={"x": 2})
    run_case(svc, monkeypatch, body)


@pytest.mark.parametrize("value", ["on", "off"])
def test_forged_marker_in_stored_schedule_or_template_is_dropped(svc, monkeypatch, value):
    switch(svc, monkeypatch, value)

    async def body(call, w):
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select

        async def prod(ids):
            return bool(set(ids) & w.production)
        monkeypatch.setattr(svc.scheduler, "any_production_hosts", prod)
        runs = {}
        for host in (H1, H2):                  # not production: runs at once; production: waits
            t = await template(call, name=f"t-{host}", script_content="true", target_host_ids=[host])
            await forge_template(svc, t["id"])
            sid = f"s-{host[:8]}"
            async with svc.db.SessionLocal() as s:
                s.add(svc.models.ZASchedule(id=sid, job_template_id=t["id"], name="s", cron_expression="* * * * *",
                                            params_override=FORGED, enabled=True, created_by="sue", editor_ids=["sue"],
                                            next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
                await s.commit()
            runs[host] = f"schedule:{sid}"
        await svc.scheduler._tick({"bash_script": SimpleNamespace(name="bash_script")}, svc.config.get_settings())
        await asyncio.sleep(0.02)
        async with svc.db.SessionLocal() as s:
            got = {h: (await s.execute(select(svc.models.ZAJobRun).where(svc.models.ZAJobRun.triggered_by == tb))).scalar_one()
                   for h, tb in runs.items()}
        assert got[H1].status == "pending" and got[H2].status == "pending_approval"
        for run in got.values():
            assert no_marker(svc, run.params), run.params
            assert run.params["_accountable_user_ids"] == ["sue"], "the code's own value, not the stored one"
    run_case(svc, monkeypatch, body)


def test_forged_marker_never_reaches_manual_internal_hook_or_chain_runs(svc, monkeypatch):
    switch(svc, monkeypatch, "on")

    async def body(call, w):
        import app._production_gate as gate
        import libs.pluginbase as pb

        async def prod(ids):
            return bool(set(ids) & w.production)
        monkeypatch.setattr(gate, "any_production_hosts", prod)
        t = await template(call, script_content="true")
        await forge_template(svc, t["id"])
        manual = await call("POST", f"/job-templates/{t['id']}/run", code=202, target_host_ids=[H1])
        await asyncio.sleep(0.02)
        w.block.set()
        await asyncio.sleep(0.02)
        svc.runner.LIVE_RUNS.clear()
        async with svc.db.SessionLocal() as s:
            (await s.get(svc.models.ZAJobRun, manual["run_id"])).status = "success"
            await s.commit()
        internal = await call("POST", "/internal/job-runs", code=202, job_template_id=t["id"], triggered_by="zabbix_event:1",
                              target_host_ids=[H2], params={}, accountable_user_ids=["alice"])
        got = await call("GET", f"/internal/posture/hook-templates/{t['id']}")
        hook = await call("POST", "/internal/posture/hook-runs", code=202, job_template_id=t["id"],
                          expect_updated_at=got["updated_at"], host_id=H1, requested_by="alice")
        for rid in (manual["run_id"], internal["run_id"], hook["run_id"]):
            assert no_marker(svc, (await get(svc, rid)).params), rid
        assert (await get(svc, internal["run_id"])).status == "pending_approval"
        # chain step built from the same template
        seen = []

        async def execute(run_id, executor, request, **kw):
            seen.append(request.params)
            async with svc.db.SessionLocal() as s:
                (await s.get(svc.models.ZAJobRun, run_id)).status = "success"
                await s.commit()
        monkeypatch.setattr(svc.runner, "execute", execute)
        monkeypatch.setattr(pb, "discover_plugins", lambda *x: {"bash_script": SimpleNamespace(name="bash_script")})

        async def line(text, *x):
            pass
        res = await svc.chain.ChainExecutor().execute(pb.RunRequest(
            action_type="chain", target_host_ids=[], credential_id=None, triggered_by="user:alice",
            params={"_run_id": "p", "_chain_steps": [{"template_id": t["id"]}]}), line)
        assert res.ok and len(seen) == 1 and no_marker(svc, seen[0])
    run_case(svc, monkeypatch, body)


def test_an_approved_run_never_also_claims_approval_was_not_required(svc, monkeypatch):
    async def body(call, w):
        t = await template(call, script_content="true", target_host_ids=[H2])
        r = await call("POST", f"/job-templates/{t['id']}/run", code=202, target_host_ids=[H2])
        async with svc.db.SessionLocal() as s:
            run = await s.get(svc.models.ZAJobRun, r["run_id"])
            run.params = {**run.params, **FORGED}
            await s.commit()
        await call("POST", f"/job-runs/{r['run_id']}/approve", uid="bob", code=202)
        params = (await get(svc, r["run_id"])).params
        assert params["_approved_by"] == "bob" and no_marker(svc, params)
    run_case(svc, monkeypatch, body)
