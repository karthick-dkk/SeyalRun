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
        for r in (svc.internal.router, svc.runs.router, svc.templates.router):
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
