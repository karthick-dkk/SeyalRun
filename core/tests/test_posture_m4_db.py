"""posture M3/M4 on a REAL PostgreSQL: the rollout engine and API driven over ASGI
against simulated hosts. Only sibling services are faked.

Opt-in, same variables as test_posture_m2_db.py (POSTURE_TEST_DB_HOST ...).
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import importlib
import importlib.util
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

if not os.environ.get("POSTURE_TEST_DB_HOST"):
    pytest.skip("POSTURE_TEST_DB_HOST not set (opt-in DB test)", allow_module_level=True)
for _mod in ("fastapi", "sqlalchemy", "asyncpg", "alembic", "multipart"):
    pytest.importorskip(_mod)

CORE = Path(__file__).resolve().parent.parent
SVC = CORE / "services/posture-service"
SECRET = "m4-test-secret-" + "x" * 32
H = [f"{i:08d}-0000-4000-8000-000000000000" for i in range(6)]
DB = "posture_m4_test"


def payload(kind: str, recs: list[str]) -> list[str]:
    raw = base64.b64encode(gzip.compress("\n".join([*recs, "Z\tend"]).encode())).decode()
    return [f"[host:x] SEYALRUN-{kind}-BEGIN v1 gz", f"[host:x] {raw}", f"[host:x] SEYALRUN-{kind}-END"]


class World:
    """Simulated hosts behind a simulated automation-service and Zabbix."""

    def __init__(self):
        self.boot = {h: 1 for h in H}
        self.version = {h: "1.0" for h in H}
        self.services = {h: {"sshd.service", "app.service"} for h in H}
        self.break_service: set[str] = set()     # app.service dies in the patch
        self.no_upgrade: set[str] = set()        # upgrade "succeeds" but changes nothing
        self.low_disk: set[str] = set()
        self.tokens: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.maintenance: dict[str, str] = {}
        self.problems: dict[str, list] = {}
        self.forget: set[str] = set()            # op keys whose first run is left dead
        self.zabbix_down = False
        self.args: list[tuple] = []
        self.undrain_broken = False
        self.posts = 0

    def _finish(self, rid, lines, rc=0):
        self.runs[rid] = {"status": "success" if rc == 0 else "failed", "exit_code": rc, "output_lines": lines, "live": False}

    async def host_op_run(self, *, action, op, args, host_id, token, requested_by, approval, accountable=None):
        rid = str(uuid.uuid4())
        self.calls.append((op, host_id, token, approval))
        self.args.append((op, host_id, args, accountable))
        if op in self.forget:
            self.forget.discard(op)
            self.tokens.setdefault(token, {})
            # one run left behind by a restart, one ended by the job time limit
            self.runs[rid] = {"status": "running" if op == "upgrade" else "error", "live": False, "output_lines": []}
            return {"run_id": rid, "status": "pending"}
        applied = self.tokens.setdefault(token, {})
        if op == "snapshot":
            free = 10 if host_id in self.low_disk else 5_000_000
            recs = ["V\t1", f"B\tboot{self.boot[host_id]}", "K\tk", "I\tsystemd", f"A\t/\t{free}", "L\ttcp:0.0.0.0:22",
                    "D\t/", "U\t0", *[f"S\t{s}" for s in sorted(self.services[host_id])]]
            self._finish(rid, payload("SNAP", recs))
        elif op == "upgrade":
            recs = applied.get("recs")
            if recs is None:
                recs = ["M\tapt"]
                if host_id not in self.no_upgrade:
                    recs += [f"-\topenssl\tamd64\t{self.version[host_id]}", "+\topenssl\tamd64\t2.0"]
                    self.version[host_id] = "2.0"
                if host_id in self.break_service:
                    self.services[host_id].discard("app.service")
                applied["recs"] = recs
            self._finish(rid, payload("PKG", recs))
        elif op == "rollback":
            self.version[host_id] = args["packages"][0]["version"]
            self._finish(rid, payload("PKG", ["M\tapt"]))
        elif op == "reboot":
            if "rebooted" not in applied:
                self.boot[host_id] += 1
                applied["rebooted"] = True
            self._finish(rid, ["SEYALRUN-REBOOTED"])
        else:
            self._finish(rid, payload("PKG", ["M\tapt"]))
        return {"run_id": rid, "status": "pending"}

    async def hook_run(self, *, template_id, expect_updated_at, host_id, requested_by, approval, accountable=None):
        self.args.append((template_id, host_id, {}, accountable))
        if self.undrain_broken and template_id == "undrain-1":
            raise self.upstream(409, "hook template changed")
        rid = str(uuid.uuid4())
        self.calls.append((template_id, host_id, "", approval))
        self._finish(rid, ["ok"])
        return {"run_id": rid, "status": "pending"}

    async def hook_template(self, tid):
        return {"id": tid, "name": tid, "updated_at": "t1", "enabled": True, "requires_approval": False,
                "editor_ids": ["hook-editor"]}

    async def job_run(self, rid):
        return self.runs.get(rid)

    async def notify(self, **kw):
        self.calls.append(("notify", kw["title"], "", None))

    def upstream(self, code, detail):
        return self.UpstreamError(code, detail)

    async def zabbix(self, method, path, *, json_body=None, params=None):
        if self.zabbix_down:
            raise self.upstream(424, "Zabbix API is not configured")
        if method == "POST":
            self.posts += 1
            mid = next((k for k, v in self.maintenance.items() if v == json_body["host_id"]), str(self.posts + 100))
            self.maintenance[mid] = json_body["host_id"]
            return {"maintenance_id": mid}
        if method == "DELETE":
            self.maintenance.pop(path.rsplit("/", 1)[1], None)
            return {"ended": True}
        return {"problems": self.problems.get(params["host_id"], [])}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    e = {"DB_ENGINE": "postgres", "DB_HOST": os.environ["POSTURE_TEST_DB_HOST"],
         "DB_PORT": os.environ.get("POSTURE_TEST_DB_PORT", "5432"), "DB_USER": os.environ.get("POSTURE_TEST_DB_USER", "postgres"),
         "DB_PASSWORD": os.environ.get("POSTURE_TEST_DB_PASSWORD", "postgres"), "POSTURE_DB_NAME": DB, "DB_SSLMODE": "disable",
         "SERVICE_JWT_SECRET": SECRET, "POSTURE_DATA_DIR": str(tmp_path_factory.mktemp("d")),
         "LOG_PATH": str(tmp_path_factory.mktemp("l") / "p.jsonl")}
    old = {k: os.environ.get(k) for k in e}
    os.environ.update(e)
    import asyncpg

    async def reset():
        conn = await asyncpg.connect(host=e["DB_HOST"], port=int(e["DB_PORT"]), user=e["DB_USER"], password=e["DB_PASSWORD"], database="postgres")
        await conn.execute(f'DROP DATABASE IF EXISTS "{DB}"')
        await conn.close()
    asyncio.run(reset())
    for step in (["upgrade", "head"], ["downgrade", "posture_002"], ["upgrade", "head"]):
        r = subprocess.run([sys.executable, "-m", "alembic", *step], cwd=SVC, capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": f"{CORE}:{SVC}"})
        assert r.returncode == 0, r.stderr[-3000:]
    yield e
    for k, v in old.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _pkg(sub: str):
    if "posture_m4" not in sys.modules:
        spec = importlib.util.spec_from_file_location("posture_m4", SVC / "app/__init__.py", submodule_search_locations=[str(SVC / "app")])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["posture_m4"] = mod
        spec.loader.exec_module(mod)
    return importlib.import_module(f"posture_m4.{sub}")


def _headers(uid, role="admin", real=None, kiosk=""):
    from libs.servicetoken import mint

    return {"X-Service-Token": mint("api-gateway", "posture-service", SECRET), "X-User-Id": uid, "X-User-Name": uid,
            "X-User-Role": role, "X-User-Real-Role": real or role,
            **({"X-Kiosk-Host-Id": kiosk} if kiosk else {})}


def run_case(env, monkeypatch, body):
    import httpx

    _pkg("config").get_settings.cache_clear()
    main, clients, audit = _pkg("main"), _pkg("clients"), _pkg("audit")
    engine, models, scanning = _pkg("rollout_engine"), _pkg("models"), _pkg("scanning")
    from sqlalchemy import delete, insert

    w = World()
    w.UpstreamError = clients.UpstreamError
    w.audit: list[dict] = []
    inventory = [{"id": h, "name": f"h{i}", "ip": "10.0.0.1", "os_type": "linux", "enabled": True, "is_production": i == 0,
                  "group_ids": ["g1", "clu"] if i < 2 else ["g1"]} for i, h in enumerate(H)]

    async def fake_log(**kw):
        w.audit.append(kw)

    async def inv_hosts():
        return inventory

    async def inv_host(hid):
        return next((h for h in inventory if h["id"] == hid), None)

    async def visible_ids(caller):
        return None if caller.sees_all_hosts else {H[5]}

    async def visible_hosts(caller):
        ids = await visible_ids(caller)
        return [h for h in inventory if ids is None or h["id"] in ids]

    monkeypatch.setattr(audit, "log_action", fake_log)
    for name, fn in (("inventory_hosts", inv_hosts), ("inventory_host", inv_host), ("visible_host_ids", visible_ids),
                     ("visible_hosts", visible_hosts), ("host_op_run", w.host_op_run), ("hook_run", w.hook_run),
                     ("hook_template", w.hook_template), ("job_run", w.job_run), ("notify", w.notify), ("zabbix", w.zabbix)):
        monkeypatch.setattr(clients, name, fn)

    async def collect(hosts, user_id, approval=None):
        """Facts collection plus the native rescan, from the simulated host."""
        hid = hosts[0]["id"]
        w.calls.append(("facts", hid, "", approval))

        async def land():
            await asyncio.sleep(0.02)
            async with scanning.SessionLocal() as s:
                row = await s.get(models.PostureHost, hid)
                row.collect_state, row.last_result, row.collected_at = "idle", "ok", datetime.now(UTC)
                await s.execute(delete(models.PostureInstalledPackage).where(models.PostureInstalledPackage.host_id == hid))
                s.add(models.PostureInstalledPackage(host_id=hid, name="openssl", arch="amd64", version=w.version[hid], source="openssl", source_version=""))
                f = await s.get(models.PostureFinding, f"f-{hid}"[:36])
                if f is not None and w.version[hid] == "2.0":
                    f.status, f.fixed_at = "fixed", datetime.now(UTC)
                s.add(models.PostureScan(id=str(uuid.uuid4()), scanner="native", kind="native", host_id=hid, status="ok",
                                         started_at=datetime.now(UTC), finished_at=datetime.now(UTC)))
                await s.commit()
        asyncio.get_running_loop().create_task(land())
        return [{"host_id": hid, "run_id": str(uuid.uuid4()), "status": "pending", "reused": False}]

    monkeypatch.setattr(_pkg("collector"), "request_collection", collect)

    async def main_():
        async with scanning.SessionLocal() as s:
            for m in (models.RolloutEvent, models.RolloutHost, models.PatchRollout, models.PatchBaseline, models.PatchWindow,
                      models.PatchRepo, models.PackageOp, models.PostureFinding, models.PostureScan, models.PostureHost,
                      models.PostureInstalledPackage, models.PosturePendingUpdate):
                await s.execute(delete(m))
            for i, h in enumerate(H):
                s.add(models.PostureHost(host_id=h, hostname=f"h{i}", distro_id="debian", distro_family="debian" if i % 2 else "rhel",
                                         distro_version="12", pkg_manager="dpkg", collected_at=datetime.now(UTC), last_result="ok",
                                         reboot_required=True, warnings=[]))
            await s.execute(insert(models.PostureInstalledPackage), [{"host_id": h, "name": "openssl", "version": "1.0", "arch": "amd64", "source": "openssl", "source_version": ""} for h in H])
            await s.execute(insert(models.PosturePendingUpdate), [{"host_id": h, "name": "openssl", "arch": "amd64", "current": "1.0", "available": "2.0", "security": True, "source": "x-security"} for h in H])
            await s.commit()
            for h in H:
                s.add(finding(models, h))
            await s.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
            async def api(method, path, uid="alice", code=200, role="admin", real=None, kiosk="", **json):
                r = await c.request(method, f"/api/v1/posture{path}", headers=_headers(uid, role, real, kiosk), json=json or None)
                assert r.status_code == code, (method, path, r.status_code, r.text)
                return r.json() if r.content and "json" in r.headers.get("content-type", "") else r

            async def drive(rid, n=60, until=("completed", "halted", "cancelled")):
                for _ in range(n):
                    await engine.tick()
                    await asyncio.sleep(0.03)
                    d = await api("GET", f"/patches/{rid}")
                    if d["status"] in until and not d["counts"].get("running"):
                        return d
                return d
            await body(w, api, drive, engine, models, scanning)

    async def whole():
        try:
            await main_()
        finally:
            await scanning.SessionLocal.kw["bind"].dispose()
    asyncio.run(whole())


def finding(models, h):
    now = datetime.now(UTC)
    want = {"id": f"f-{h}"[:36], "host_id": h, "cve_id": "CVE-2026-1", "package": "openssl", "installed_version": "1.0",
            "fixed_version": "2.0", "status": "open", "severity": "high", "first_seen": now, "last_seen": now}
    cols = models.PostureFinding.__table__.columns
    return models.PostureFinding(**{k: v for k, v in want.items() if k in cols})


CFG = {"soak_minutes": 0, "canary_size": 2, "second_percent": 50, "cluster_group_ids": ["clu"],
       "drain_template_id": "drain-1", "undrain_template_id": "undrain-1"}


async def make(api, **kw):
    r = await api("POST", "/patches", code=201, name="r", group_ids=["g1"], ignore_window=True, config={**CFG, **kw})
    await api("POST", f"/patches/{r['id']}/approve", uid="bob", plan_hash=r["plan_hash"])
    return r["id"]


def test_rollout_patches_reboots_rescans_and_closes(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        d = await drive(await make(api))
        assert d["status"] == "completed", [d["waiting"], d["counts"], *[(h["wave"], h["status"], h["step"], h["error"]) for h in d["host_rows"]]]
        assert d["counts"] == {"done": 6} and d["waves"] >= 3
        waves = {h["host_id"]: h["wave"] for h in d["host_rows"]}
        assert waves[H[0]] != waves[H[1]], "cluster peers in one wave"
        assert all(v == "2.0" for v in w.version.values()) and all(v == 2 for v in w.boot.values())
        assert w.maintenance == {} and not any(h["drained"] for h in d["host_rows"])
        ev = (await api("GET", f"/patches/{d['id']}/evidence"))["rows"]
        assert len(ev) == 6 and all(e["finding_status"] == "fixed" and e["after"] == "2.0" for e in ev)
        # Every run on a host carries the rollout's approval.
        assert all(a["approved_by"] == "bob" and "alice" in a["accountable_user_ids"] for op, _, _, a in w.calls if op != "notify")
        order = [op for op, h, _, _ in w.calls if h == H[0]]
        assert order == ["repo_apply", "snapshot", "drain-1", "upgrade", "facts", "reboot", "snapshot", "facts", "undrain-1"], order
        csvr = await api("GET", f"/patches/{d['id']}/evidence?format=csv")
        assert "CVE-2026-1" in csvr.text and "bob" in csvr.text
    run_case(env, monkeypatch, body)


def test_failed_post_check_halts_and_leaves_host_drained(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        w.break_service = set(H)
        real = engine.timedelta
        monkeypatch.setattr(engine, "timedelta", lambda **k: real(0) if k == {"seconds": 45} else real(**k))
        rid = await make(api)
        d = await drive(rid, n=120)
        assert d["status"] == "halted" and "app.service" in next(h["error"] for h in d["host_rows"] if h["status"] == "failed")
        assert d["counts"]["pending"] == 4, "hosts after the canary wave must not be touched"
        bad = [h for h in d["host_rows"] if h["status"] == "failed"]
        assert len(bad) == 2 and all(h["drained"] and h["in_maintenance"] for h in bad)
        assert len(w.maintenance) == 2 and not any(op == "undrain-1" for op, *_ in w.calls)
        # it does not resume while the failure stands, and the claim blocks other rollouts
        await api("POST", f"/patches/{rid}/resume", code=409, reason="try again")
        await api("POST", "/patches", code=409, name="other", host_ids=[bad[0]["host_id"]], ignore_window=True)
        hid = bad[0]["host_id"]
        await api("POST", f"/patches/{rid}/hosts/{hid}/skip", code=409, reason="skip it")
        # rollback puts the old version back and leaves it drained
        await api("POST", f"/patches/{rid}/hosts/{hid}/rollback", reason="put back")
        for _ in range(5):
            await engine.tick()
        d = await api("GET", f"/patches/{rid}")
        row = next(h for h in d["host_rows"] if h["host_id"] == hid)
        assert row["status"] == "rolled_back" and row["drained"] and w.version[hid] == "1.0"
        await api("POST", f"/patches/{rid}/hosts/{hid}/release", reason="checked by hand")
        for _ in range(5):
            await engine.tick()
        row = next(h for h in (await api("GET", f"/patches/{rid}"))["host_rows"] if h["host_id"] == hid)
        assert row["status"] == "rolled_back" and not row["drained"] and not row["in_maintenance"]
        await api("POST", f"/patches/{rid}/cancel", reason="enough")
        # the rolled-back host is free again; the other one is still drained, so it is not
        await api("POST", "/patches", code=201, name="again", host_ids=[hid], ignore_window=True)
        other = next(h["host_id"] for h in bad if h["host_id"] != hid)
        await api("POST", "/patches", code=409, name="again2", host_ids=[other], ignore_window=True)
        await api("POST", f"/patches/{rid}/hosts/{other}/release", reason="checked by hand")
        for _ in range(5):
            await engine.tick()
        await api("POST", "/patches", code=201, name="again3", host_ids=[other], ignore_window=True)
        assert w.maintenance == {}
    run_case(env, monkeypatch, body)


def test_pre_check_failure_changes_nothing(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        w.low_disk = set(H)
        d = await drive(await make(api))
        assert d["status"] == "halted" and "disk /" in d["host_rows"][0]["error"]
        assert {op for op, *_ in w.calls} == {"repo_apply", "snapshot", "notify"} and w.maintenance == {}
    run_case(env, monkeypatch, body)


def test_upgrade_that_changes_nothing_is_not_success(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        w.no_upgrade = set(H)
        d = await drive(await make(api))
        assert d["status"] == "halted" and "planned 2.0" in d["host_rows"][0]["error"]
    run_case(env, monkeypatch, body)


def test_lost_run_is_restarted_with_the_same_token(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        w.forget = {"upgrade", "reboot"}
        monkeypatch.setattr(engine, "RELAUNCH_AFTER", engine.timedelta(0))
        d = await drive(await make(api))
        assert d["status"] == "completed" and all(v == 2 for v in w.boot.values())
        for op in ("upgrade", "reboot"):
            toks = [t for o, _, t, _ in w.calls if o == op]
            assert len(toks) == 7 and len(set(toks)) == 6
    run_case(env, monkeypatch, body)


def test_new_zabbix_problem_during_soak_halts(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        rid = await make(api)
        orig = w.host_op_run

        async def op_run(**kw):
            if kw["op"] == "reboot":
                w.problems[kw["host_id"]] = [{"trigger_id": "9", "name": "disk latency"}]
            return await orig(**kw)
        monkeypatch.setattr(_pkg("clients"), "host_op_run", op_run)
        d = await drive(rid)
        assert d["status"] == "halted" and "Zabbix problem" in d["status_reason"]
        assert any(e["event"] == "new_zabbix_problem" and "disk latency" in e["detail"] and e["host_id"] for e in d["events"]) and d["counts"]["pending"] == 4
    run_case(env, monkeypatch, body)


def test_zabbix_optional_and_required(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        w.zabbix_down = True
        d = await drive(await make(api))
        assert d["status"] == "completed"
        assert any(c["name"] == "Zabbix maintenance" and c["ok"] is None for c in d["host_rows"][0]["checks"])
    run_case(env, monkeypatch, body)

    async def body2(w, api, drive, engine, models, scanning):
        w.zabbix_down = True
        d = await drive(await make(api, zabbix="required"))
        assert d["status"] == "halted" and not any(op == "upgrade" for op, *_ in w.calls)
    run_case(env, monkeypatch, body2)


def test_approval_rules(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        b = await api("POST", "/patch-baselines", uid="carol", code=201, name="b", group_ids=["g1"], config=CFG)
        r = await api("POST", "/patches", code=201, name="r", baseline_id=b["id"], ignore_window=True)
        rid, good = r["id"], r["plan_hash"]
        await api("POST", f"/patches/{rid}/approve", uid="alice", code=403, plan_hash=good)      # creator
        await api("POST", f"/patches/{rid}/approve", uid="carol", code=403, plan_hash=good)      # edited the baseline
        await api("POST", f"/patches/{rid}/approve", uid="hook-editor", code=403, plan_hash=good)
        await api("POST", f"/patches/{rid}/approve", uid="bob", code=409, plan_hash="0" * 64)    # not what was reviewed
        await api("POST", f"/patches/{rid}/approve", uid="op", role="operator", code=403, plan_hash=good)
        await api("POST", f"/patches/{rid}/approve", uid="op", role="admin", real="operator", code=403, plan_hash=good)
        await engine.tick()
        assert w.calls == [c for c in w.calls if c[0] == "notify"], "nothing runs before approval"
        await api("PUT", f"/patch-baselines/{b['id']}", uid="dave", name="b", group_ids=["g1"], config={"soak_minutes": 0, "reboot": "never"})
        d = await api("GET", f"/patches/{rid}")
        assert d["plan_hash"] == good and d["config"]["reboot"] == "if_required", "the rollout kept its own copy"
        # the plan row was changed behind the approver's back
        async with scanning.SessionLocal() as s:
            row = await s.get(models.PatchRollout, rid)
            row.plan = {**row.plan, "config": {**row.plan["config"], "reboot": "never"}}
            await s.commit()
        await api("POST", f"/patches/{rid}/approve", uid="bob", code=409, plan_hash=good)
        await api("POST", f"/patches/{rid}/reject", uid="alice", reason="start over")
        r2 = await api("POST", "/patches", code=201, name="r2", host_ids=[H[5]], ignore_window=True, config=CFG)
        assert r2["hosts"] == 1
        assert any(a["action"] == "posture.patch.create" and a.get("critical") for a in w.audit)
        # visibility
        assert len((await api("GET", "/patches", uid="v", role="viewer"))["items"]) == 2
        seen = await api("GET", f"/patches/{rid}", uid="v", role="viewer")
        assert [h["host_id"] for h in seen["host_rows"]] == [H[5]]
        await api("POST", "/patches", uid="v", role="operator", code=403, name="x", host_ids=[H[5]], ignore_window=True)
        await api("POST", "/patches", code=400, name="x", host_ids=[H[4]], config=CFG)           # no window chosen
        await api("POST", "/patches", code=400, name="x", host_ids=[H[4]], ignore_window=True, config={"max_failuers": 1})
    run_case(env, monkeypatch, body)


def test_unrecordable_change_is_refused(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        audit = _pkg("audit")

        async def broken(**kw):
            if kw.get("critical"):
                raise audit.AuditWriteError("down")
        monkeypatch.setattr(audit, "log_action", broken)
        await api("POST", "/patches", code=503, name="r", group_ids=["g1"], ignore_window=True, config=CFG)
        assert (await api("GET", "/patches"))["items"] == []
        await api("POST", f"/hosts/{H[5]}/packages", code=503, op="upgrade", packages=[{"name": "openssl"}])
        assert w.calls == []
    run_case(env, monkeypatch, body)


def test_window_gates_start_and_new_hosts(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        core = _pkg("rollout_core")
        win = await api("POST", "/patch-windows", uid="carol", code=201, name="w", timezone="Asia/Kolkata", days=[0], start="02:00", duration_minutes=60)
        monkeypatch.setattr(core, "window_end", lambda *_: None)
        r = await api("POST", "/patches", code=201, name="r", group_ids=["g1"], window_id=win["id"], config=CFG)
        await api("POST", f"/patches/{r['id']}/approve", uid="carol", code=403, plan_hash=r["plan_hash"])
        await api("POST", f"/patches/{r['id']}/approve", uid="bob", plan_hash=r["plan_hash"])
        await engine.tick()
        d = await api("GET", f"/patches/{r['id']}")
        assert d["status"] == "approved" and "waiting for window" in d["waiting"]
        monkeypatch.setattr(core, "window_end", lambda *_: datetime.now(UTC))
        for _ in range(4):
            await engine.tick()
        monkeypatch.setattr(core, "window_end", lambda *_: None)
        d = await drive(r["id"], n=30, until=())
        assert d["status"] == "running" and d["counts"] == {"done": 2, "pending": 4} and "is closed" in d["waiting"]
    run_case(env, monkeypatch, body)


def test_package_operation_on_one_host(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        key = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nx\n-----END PGP PUBLIC KEY BLOCK-----"
        await api("POST", "/patch-repos", code=400, slug="m", family="deb", base_url="http://m/d", suite="s", gpg_key="")
        await api("POST", "/patch-repos", code=400, slug="m", family="deb", base_url="http://u:p@m/d", suite="s", gpg_key=key)
        repo = {"slug": "m", "family": "deb", "base_url": "http://m/d", "suite": "s", "components": "main", "gpg_key": key, "group_ids": ["g1"]}
        await api("POST", "/patch-repos", code=201, **repo)
        await api("POST", "/patch-repos", code=409, **repo)
        await api("POST", f"/hosts/{H[5]}/packages", uid="op", role="operator", code=403, op="upgrade", packages=[{"name": "openssl"}])
        o = await api("POST", f"/hosts/{H[5]}/packages", code=202, op="upgrade", packages=[{"name": "openssl"}])
        await engine.tick()
        row = (await api("GET", "/package-ops"))[0]
        assert row["id"] == o["id"] and row["status"] == "ok" and row["result"]["changes"][0]["new"] == "2.0"
        assert (await api("GET", "/package-ops", uid="v", role="viewer"))[0]["id"] == o["id"]
    run_case(env, monkeypatch, body)


def fast(engine, monkeypatch):
    real = engine.timedelta
    monkeypatch.setattr(engine, "timedelta", lambda **k: real(0) if k == {"seconds": 45} else real(**k))


async def settle(engine, n=8):
    for _ in range(n):
        await engine.tick()
        await asyncio.sleep(0.03)


def test_retry_still_checks_the_whole_change(env, monkeypatch):
    """A retry whose upgrade is a no-op must not call a broken host healthy."""
    async def body(w, api, drive, engine, models, scanning):
        fast(engine, monkeypatch)
        w.break_service = {H[0], H[1], H[2], H[3], H[4], H[5]}
        rid = await make(api)
        d = await drive(rid, n=120)
        bad = [h["host_id"] for h in d["host_rows"] if h["status"] == "failed"]
        opened = w.posts
        # not repaired: the retry fails again, and the rollback information is still there
        await api("POST", f"/patches/{rid}/hosts/{bad[0]}/retry", reason="try again")
        row = next(h for h in (await api("GET", f"/patches/{rid}"))["host_rows"] if h["host_id"] == bad[0])
        assert row["status"] == "pending", "a retry waits for the window and for resume"
        await settle(engine, 3)
        assert next(h for h in (await api("GET", f"/patches/{rid}"))["host_rows"] if h["host_id"] == bad[0])["status"] == "pending"
        for h in bad[1:]:
            await api("POST", f"/patches/{rid}/hosts/{h}/retry", reason="try again")
        await api("POST", f"/patches/{rid}/resume", reason="carry on")
        d = await drive(rid, n=160)
        row = next(h for h in d["host_rows"] if h["host_id"] == bad[0])
        assert d["status"] == "halted" and row["status"] == "failed" and "app.service" in row["error"]
        assert row["changes"] == [{"name": "openssl", "arch": "amd64", "old": "1.0", "new": "2.0"}]
        assert set(w.maintenance.values()) == set(bad) and w.posts > opened, "one maintenance per host, reused"
        # repaired: now it passes
        w.break_service = set()
        for h in bad:
            w.services[h].add("app.service")
            await api("POST", f"/patches/{rid}/hosts/{h}/retry", reason="repaired")
        await api("POST", f"/patches/{rid}/resume", reason="carry on")
        d = await drive(rid, n=200)
        assert d["status"] == "completed" and w.maintenance == {}
    run_case(env, monkeypatch, body)


def test_rollback_ends_with_the_rollout(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        rid = await make(api)
        assert (await drive(rid))["status"] == "completed"
        await api("POST", f"/patches/{rid}/hosts/{H[0]}/rollback", code=409, reason="much later")
        await api("POST", f"/patches/{rid}/hosts/{H[0]}/retry", code=409, reason="much later")
    run_case(env, monkeypatch, body)


def test_force_release_frees_a_host_whose_undrain_cannot_run(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        fast(engine, monkeypatch)
        w.break_service, w.undrain_broken = set(H), True
        rid = await make(api)
        await drive(rid, n=120)
        hid = next(h["host_id"] for h in (await api("GET", f"/patches/{rid}"))["host_rows"] if h["status"] == "failed")
        await api("POST", f"/patches/{rid}/hosts/{hid}/release", reason="checked")
        await settle(engine)
        row = next(h for h in (await api("GET", f"/patches/{rid}"))["host_rows"] if h["host_id"] == hid)
        assert row["status"] == "failed" and row["drained"]
        await api("POST", f"/patches/{rid}/hosts/{hid}/force_release", uid="op", role="operator", code=403, reason="by hand")
        await api("POST", f"/patches/{rid}/hosts/{hid}/force_release", reason="undrained by hand")
        assert any(a["action"] == "posture.patch.host_force_release" and a.get("critical") for a in w.audit)
        await api("POST", f"/patches/{rid}/hosts/{hid}/skip", reason="left out")
        await api("POST", "/patches", code=201, name="again", host_ids=[hid], ignore_window=True)
    run_case(env, monkeypatch, body)


def test_approved_versions_are_what_is_requested(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        await drive(await make(api))
        ups = [a for op, _, a, _ in w.args if op == "upgrade"]
        assert ups and all(p == {"name": "openssl", "arch": "amd64", "version": "2.0"} for a in ups for p in a["packages"])
    run_case(env, monkeypatch, body)


def test_damaged_output_of_one_host_does_not_stop_the_engine(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        o = await api("POST", f"/hosts/{H[5]}/packages", code=202, op="upgrade", packages=[{"name": "openssl"}])
        good = gzip.compress(b"M\tapt\nZ\tend")
        broken = base64.b64encode(good[:10] + b"\xff" * 8).decode()     # zlib.error, not OSError
        w.runs[o["job_run_id"]]["output_lines"] = ["SEYALRUN-PKG-BEGIN v1 gz", broken, "SEYALRUN-PKG-END"]
        o2 = await api("POST", f"/hosts/{H[4]}/packages", code=202, op="upgrade", packages=[{"name": "openssl"}])
        raw = base64.b64encode(b"M\nZ\tend").decode()
        w.runs[o2["job_run_id"]]["output_lines"] = ["SEYALRUN-PKG-BEGIN v1 raw", raw, "SEYALRUN-PKG-END"]
        assert (await drive(await make(api)))["status"] == "completed"
        assert {x["status"] for x in await api("GET", "/package-ops")} == {"failed"}
    run_case(env, monkeypatch, body)


def test_approved_package_op_is_followed_not_requested_again(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        monkeypatch.setattr(engine, "RELAUNCH_AFTER", engine.timedelta(0))
        o = await api("POST", f"/hosts/{H[5]}/packages", code=202, op="upgrade", packages=[{"name": "openssl"}])
        run = w.runs[o["job_run_id"]]
        done = dict(run)
        n = len(w.calls)
        run.update(status="running", live=True)
        await engine.tick()
        assert len(w.calls) == n, "a live run is only watched"
        run.update(status="error", live=False)          # the follower died
        await engine.tick()
        assert [c[2] for c in w.calls[n:]] == [f"op-{o['id']}"], "same token"
        await engine.tick()
        assert (await api("GET", "/package-ops"))[0]["status"] == "ok" and done
    run_case(env, monkeypatch, body)


def test_mirror_editors_are_accountable_for_package_ops(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        key = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nx\n-----END PGP PUBLIC KEY BLOCK-----"
        await api("POST", "/patch-repos", uid="carol", code=201, slug="m", family="deb", base_url="http://m/d", suite="s",
                  components="main", gpg_key=key, group_ids=["g1"])
        o = await api("POST", f"/hosts/{H[5]}/packages", code=202, op="upgrade", packages=[{"name": "openssl"}])
        assert w.args[-1][3] == ["carol"] and "accountable" not in o["result"]
    run_case(env, monkeypatch, body)


def test_hook_interrupted_by_a_restart_is_not_run_again(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        clients = _pkg("clients")
        calls = []

        async def dies(**kw):
            calls.append(kw)
            raise asyncio.CancelledError
        real = clients.hook_run
        monkeypatch.setattr(clients, "hook_run", dies)
        rid = await make(api, canary_size=1)
        for _ in range(8):
            try:
                await engine.tick()
            except asyncio.CancelledError:
                break
            if calls:
                break
        assert len(calls) == 1
        monkeypatch.setattr(clients, "hook_run", real)
        d = await drive(rid)
        bad = next(h for h in d["host_rows"] if h["status"] == "failed")
        assert d["status"] == "halted" and bad["drained"] and "while starting" in bad["error"]
        assert not any(op == "drain-1" for op, *_ in w.calls)
    run_case(env, monkeypatch, body)


def test_scoped_reader_learns_nothing_about_other_hosts(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        import json
        w.low_disk = set(H)
        rid = await make(api, cluster_group_ids=[])
        await drive(rid)
        seen = await api("GET", f"/patches/{rid}", uid="v", role="viewer")
        text = json.dumps(seen)
        assert not any(h in text for h in H[:5]) and not any(f'"h{i}' in text or f"h{i}:" in text for i in range(5))
        assert seen["hosts"] == 1 and sum(seen["counts"].values()) == 1
        ev = json.dumps(await api("GET", f"/patches/{rid}/evidence", uid="v", role="viewer"))
        assert not any(f'"h{i}"' in ev for i in range(5))
    run_case(env, monkeypatch, body)


def test_release_keeps_the_reason_of_the_failure(env, monkeypatch):
    async def body(w, api, drive, engine, models, scanning):
        fast(engine, monkeypatch)
        w.break_service = set(H)
        rid = await make(api)
        hid = next(h["host_id"] for h in (await drive(rid, n=120))["host_rows"] if h["status"] == "failed")
        await api("POST", f"/patches/{rid}/hosts/{hid}/release", reason="checked")
        await settle(engine)
        row = next(h for h in (await api("GET", f"/patches/{rid}"))["host_rows"] if h["host_id"] == hid)
        assert row["status"] == "failed" and not row["drained"] and "app.service" in row["error"]
    run_case(env, monkeypatch, body)


def test_kiosk_admin_is_limited_to_its_host(env, monkeypatch):
    """A kiosk login carries its user's admin role but belongs to one host."""
    async def body(w, api, drive, engine, models, scanning):
        fast(engine, monkeypatch)
        w.break_service = set(H)
        r = await api("POST", "/patches", code=201, name="r", group_ids=["g1"], ignore_window=True, config=CFG)
        rid = r["id"]
        for k in (H[0], "00000009-0000-4000-8000-000000000000"):
            await api("POST", f"/patches/{rid}/approve", uid="bob", kiosk=k, code=403, plan_hash=r["plan_hash"])
            for a in ("reject", "pause", "resume", "cancel"):
                await api("POST", f"/patches/{rid}/{a}", uid="bob", kiosk=k, code=403, reason="kiosk")
        await api("POST", "/patches", kiosk=H[0], code=403, name="k", host_ids=[H[0]], ignore_window=True)
        await api("POST", "/patch-windows", kiosk=H[0], code=403, name="k", timezone="UTC", days=[1], start="22:00", duration_minutes=60)
        await api("POST", "/patch-baselines", kiosk=H[0], code=403, name="k", config={})
        assert not (await api("GET", f"/patches/{rid}", uid="bob", kiosk=H[0]))["can_approve"]
        await api("POST", f"/patches/{rid}/approve", uid="bob", plan_hash=r["plan_hash"])
        bad = [h["host_id"] for h in (await drive(rid, n=120))["host_rows"] if h["status"] == "failed"]
        other = next(h for h in H if h not in bad)
        n = len(w.audit)
        for a in ("retry", "rollback", "release", "force_release", "skip"):
            await api("POST", f"/patches/{rid}/hosts/{bad[0]}/{a}", kiosk=other, code=404, reason="kiosk")
        assert not any(x.get("critical") for x in w.audit[n:]), "the attempts are audited, but none was carried out"
        assert {h["status"] for h in (await api("GET", f"/patches/{rid}"))["host_rows"] if h["host_id"] == bad[0]} == {"failed"}
    run_case(env, monkeypatch, body)


def test_everyone_accountable_is_named_on_every_run_of_a_rollout(env, monkeypatch):
    """A host that becomes production after planning is gated per run: that second
    approval must be barred to the same people as the rollout's own."""
    async def body(w, api, drive, engine, models, scanning):
        await drive(await make(api))
        assert {op for op, *_ in w.args} >= {"upgrade", "drain-1", "undrain-1", "repo_apply"}
        assert all({"alice", "hook-editor"} <= set(acc or []) for *_, acc in w.args)
    run_case(env, monkeypatch, body)
