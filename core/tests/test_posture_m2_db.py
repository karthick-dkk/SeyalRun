"""posture M2 end to end on a REAL PostgreSQL: alembic upgrade to head, then the
FastAPI app driven over ASGI with only the sibling services (inventory,
identity, audit forwarding) faked.

Opt-in — it needs a database and the service's own dependencies, which the
minimal security suite does not install:

    POSTURE_TEST_DB_HOST=<pg host> [POSTURE_TEST_DB_PORT=5432 POSTURE_TEST_DB_USER=postgres
    POSTURE_TEST_DB_PASSWORD=postgres POSTURE_TEST_DB_NAME=posture_m2_test]
    pip install -r core/services/posture-service/requirements.txt

What it proves beyond the in-memory tests: the SQL (bulk upserts by primary
key, the unique key, chunked IN queries, group-bys) and the HTTP layer
(contract shapes, status codes, RBAC on the real role, one audit row per call).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

if not os.environ.get("POSTURE_TEST_DB_HOST"):
    pytest.skip("POSTURE_TEST_DB_HOST not set (opt-in DB test)", allow_module_level=True)
for _mod in ("fastapi", "sqlalchemy", "asyncpg", "alembic", "multipart"):
    pytest.importorskip(_mod)

CORE = Path(__file__).resolve().parent.parent
SVC = CORE / "services/posture-service"
FIX = SVC / "tools/fixtures/scans"
SECRET = "m2-test-secret-" + "x" * 32
WEB01, DB01 = "11111111-0000-4000-8000-000000000001", "22222222-0000-4000-8000-000000000002"
INVENTORY = [{"id": WEB01, "name": "web01", "ip": "10.0.0.5", "os_type": "linux"},
             {"id": DB01, "name": "db01", "ip": "10.0.0.6", "os_type": "linux"}]


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    e = {
        "DB_ENGINE": "postgres", "DB_HOST": os.environ["POSTURE_TEST_DB_HOST"],
        "DB_PORT": os.environ.get("POSTURE_TEST_DB_PORT", "5432"),
        "DB_USER": os.environ.get("POSTURE_TEST_DB_USER", "postgres"),
        "DB_PASSWORD": os.environ.get("POSTURE_TEST_DB_PASSWORD", "postgres"),
        "POSTURE_DB_NAME": os.environ.get("POSTURE_TEST_DB_NAME", "posture_m2_test"),
        "DB_SSLMODE": "disable", "SERVICE_JWT_SECRET": SECRET,
        "POSTURE_DATA_DIR": str(tmp_path_factory.mktemp("posture-data")),
        "LOG_PATH": str(tmp_path_factory.mktemp("log") / "p.jsonl"),
    }
    os.environ.update(e)
    # fresh schema every run: drop and recreate via the migration
    import asyncpg

    async def reset():
        conn = await asyncpg.connect(host=e["DB_HOST"], port=int(e["DB_PORT"]), user=e["DB_USER"],
                                     password=e["DB_PASSWORD"], database="postgres")
        await conn.execute(f'DROP DATABASE IF EXISTS "{e["POSTURE_DB_NAME"]}"')
        await conn.close()
    asyncio.run(reset())
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SVC, capture_output=True,
                       text=True, env={**os.environ, "PYTHONPATH": f"{CORE}:{SVC}"})
    assert r.returncode == 0, r.stderr[-3000:]
    # downgrade + upgrade again: the migration is reversible and idempotent
    for step in (["downgrade", "posture_001"], ["upgrade", "head"]):
        r = subprocess.run([sys.executable, "-m", "alembic", *step], cwd=SVC, capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": f"{CORE}:{SVC}"})
        assert r.returncode == 0, r.stderr[-3000:]
    return e


def _pkg(sub: str):
    if "posture_app" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "posture_app", SVC / "app/__init__.py", submodule_search_locations=[str(SVC / "app")])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["posture_app"] = mod
        spec.loader.exec_module(mod)
    return importlib.import_module(f"posture_app.{sub}")


def test_m2_end_to_end(env, monkeypatch):
    import httpx
    from libs.servicetoken import mint

    _pkg("config").get_settings.cache_clear()
    main = _pkg("main")
    clients, audit, scanning, models = _pkg("clients"), _pkg("audit"), _pkg("scanning"), _pkg("models")
    from sqlalchemy import insert, select, update

    audit_rows: list[dict] = []

    async def fake_log(**kw):
        audit_rows.append(kw)

    async def inv_hosts():
        return INVENTORY

    async def inv_host(hid):
        return next((h for h in INVENTORY if h["id"] == hid), None)

    async def visible_ids(caller):
        return None if caller.sees_all_hosts else {WEB01}

    async def visible_hosts(caller):
        ids = await visible_ids(caller)
        return [h for h in INVENTORY if ids is None or h["id"] in ids]

    async def no_disabled():
        return set()

    monkeypatch.setattr(audit, "log_action", fake_log)
    for name, fn in (("inventory_hosts", inv_hosts), ("inventory_host", inv_host),
                     ("visible_host_ids", visible_ids), ("visible_hosts", visible_hosts),
                     ("disabled_scanners", no_disabled)):
        monkeypatch.setattr(clients, name, fn)

    def headers(role="admin", real=None, uid="u-admin"):
        return {"X-Service-Token": mint("api-gateway", "posture-service", SECRET), "X-User-Id": uid,
                "X-User-Name": uid, "X-User-Role": role, "X-User-Real-Role": real or role}

    async def scenario():
        S = scanning.SessionLocal
        now = datetime.now(UTC)
        async with S() as s:
            s.add(models.PostureHost(host_id=WEB01, hostname="web01", distro_id="debian", distro_family="debian",
                                     distro_version="12", pkg_manager="dpkg", collected_at=now, last_result="ok",
                                     warnings=[]))
            await s.execute(insert(models.PostureInstalledPackage), [
                {"host_id": WEB01, "name": "libssl3", "version": "3.0.11-1~deb12u2", "arch": "amd64",
                 "source": "openssl", "source_version": "3.0.11-1~deb12u2"},
                {"host_id": WEB01, "name": "libgnutls30", "version": "3.7.9-2+deb12u1", "arch": "amd64",
                 "source": "gnutls28", "source_version": "3.7.9-2+deb12u1"},
                {"host_id": WEB01, "name": "zlib1g", "version": "1:1.2.13.dfsg-1", "arch": "amd64",
                 "source": "zlib", "source_version": "1:1.2.13.dfsg-1"},
            ])
            snap = models.FeedSnapshot(version="m2-test-1", generated_at=now - timedelta(days=2), status="ready",
                                       active=True, manifest={}, stats={})
            s.add(snap)
            await s.flush()
            await s.execute(insert(models.FeedOsv), [
                {"snapshot_id": snap.id, "vuln_id": "DEBIAN-CVE-2023-5678", "cve_id": "CVE-2023-5678",
                 "ecosystem": "Debian:12", "package": "openssl", "range_type": "ECOSYSTEM", "introduced": "0",
                 "fixed": "3.0.13-1~deb12u1", "cvss": 5.3, "summary": "DH"}])
            await s.execute(insert(models.FeedOval), [
                {"snapshot_id": snap.id, "definition_id": "oval:d:1", "cve_id": "CVE-2024-0553",
                 "platform": "Debian GNU/Linux 12", "package": "gnutls28", "fixed_version": "3.7.9-2+deb12u2"}])
            await s.execute(insert(models.FeedEpss), [
                {"snapshot_id": snap.id, "cve_id": "CVE-2023-5678", "score": 0.002, "percentile": 0.6}])
            await s.execute(insert(models.FeedKev), [{"snapshot_id": snap.id, "cve_id": "CVE-2024-0553"}])
            await s.commit()

        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://posture") as c:
            A = headers()
            # native run
            r = await c.post("/api/v1/posture/scanners/native/run", headers=A, json={})
            assert r.status_code == 202 and r.json()["queued"] == 1, r.text
            await scanning._native_task
            r = await c.get("/api/v1/posture/findings", headers=A, params={"sort": "risk"})
            body = r.json()
            assert body["total"] == 2, body
            top = body["items"][0]
            assert top["cve_id"] == "CVE-2024-0553" and top["kev"] is True and top["risk_score"] >= 90
            assert top["package"] == "gnutls28" and top["sources"] == ["native"] and top["hostname"] == "web01"

            # vuls import: merges into the native findings
            with open(FIX / "vuls-web01.json", "rb") as f:
                r = await c.post("/api/v1/posture/scans/import", headers=A, data={"scanner": "vuls"},
                                 files={"file": ("web01.json", f, "application/json")})
            assert r.status_code == 201, r.text
            assert {k: r.json()[k] for k in ("findings_created", "findings_updated", "unmatched")} == \
                {"findings_created": 1, "findings_updated": 2, "unmatched": 0}
            r = await c.get("/api/v1/posture/findings", headers=A, params={"q": "CVE-2023-5678"})
            items = r.json()["items"]
            assert len(items) == 1 and items[0]["sources"] == ["native", "vuls"] and items[0]["host_id"] == WEB01
            assert items[0]["epss_percentile"] == 0.6
            fid = items[0]["id"]

            # generic CSV: unknown host queued, never dropped
            with open(FIX / "generic-mixed.csv", "rb") as f:
                r = await c.post("/api/v1/posture/scans/import", headers=A, data={"scanner": "generic"},
                                 files={"file": ("mixed.csv", f, "text/csv")})
            assert r.status_code == 201 and r.json()["unmatched"] == 1, r.text
            r = await c.get("/api/v1/posture/unmatched", headers=A)
            q = r.json()
            assert q["total"] == 1 and q["items"][0]["reported_host"] == "ghost.example.net"
            assert q["items"][0]["cve_count"] == 2 and q["items"][0]["reported_ip"] == "10.9.9.9"
            uid = q["items"][0]["id"]
            r = await c.post(f"/api/v1/posture/unmatched/{uid}/assign", headers=A, json={"host_id": DB01})
            assert r.status_code == 200 and r.json() == {"created_findings": 2}, r.text
            assert (await c.get("/api/v1/posture/unmatched", headers=A)).json()["total"] == 0
            r = await c.post(f"/api/v1/posture/unmatched/{uid}/assign", headers=A, json={"host_id": DB01})
            assert r.status_code == 409
            r = await c.get("/api/v1/posture/findings", headers=A, params={"host_id": DB01})
            assert {i["cve_id"] for i in r.json()["items"]} == {"CVE-2024-2961", "CVE-2021-44228", "CVE-2022-22965"}

            # detail: references filtered, history present
            r = await c.get(f"/api/v1/posture/findings/{fid}", headers=A)
            d = r.json()
            assert d["references"] and all(u.startswith(("http://", "https://")) for u in d["references"])
            assert [h["event"] for h in d["history"]][:2] == ["detected", "reported"]

            # status: admin only, reason required to accept
            r = await c.post(f"/api/v1/posture/findings/{fid}/status", headers=A, json={"status": "accepted"})
            assert r.status_code == 400
            r = await c.post(f"/api/v1/posture/findings/{fid}/status", headers=A, json={"status": "fixed", "reason": "x"})
            assert r.status_code == 422
            sup = headers(role="admin", real="support", uid="u-support")   # vouched write, real role support
            r = await c.post(f"/api/v1/posture/findings/{fid}/status", headers=sup,
                             json={"status": "accepted", "reason": "x"})
            assert r.status_code == 403
            r = await c.post(f"/api/v1/posture/findings/{fid}/status", headers=A,
                             json={"status": "accepted", "reason": "compensating control"})
            assert r.status_code == 200 and r.json()["status"] == "accepted"
            for path in ("/api/v1/posture/scans/import", "/api/v1/posture/scanners/native/run"):
                assert (await c.post(path, headers=sup, json={})).status_code == 403

            # reads for support; a narrowed role sees only its hosts and no unmatched queue
            S_ = headers(role="support", uid="u-support")
            assert (await c.get("/api/v1/posture/findings", headers=S_)).json()["total"] == 6
            N = headers(role="operator", uid="u-op")
            assert {i["host_id"] for i in (await c.get("/api/v1/posture/findings", headers=N)).json()["items"]} == {WEB01}
            assert (await c.get(f"/api/v1/posture/findings/{(await c.get('/api/v1/posture/findings', headers=A, params={'host_id': DB01})).json()['items'][0]['id']}", headers=N)).status_code == 404
            assert (await c.get("/api/v1/posture/unmatched", headers=N)).json() == {"items": [], "total": 0}

            # summaries
            sm = (await c.get("/api/v1/posture/findings/summary", headers=A)).json()
            assert sm["open_total"] == 5 and sm["kev_open"] == 1 and sm["hosts_affected"] == 2
            assert sm["feed_stale"] is False and sm["feed_age_days"] == 2 and sm["stale_after_days"] == 7
            assert sum(sm["by_severity"].values()) == 5
            ps = (await c.get("/api/v1/posture/summary", headers=A)).json()
            assert ps["findings_open"] == 5 and ps["kev_open"] == 1 and "hosts_total" in ps

            sc = {s["name"]: s for s in (await c.get("/api/v1/posture/scanners", headers=A)).json()}
            assert set(sc) == {"native", "generic", "openscap", "vuls"}
            assert sc["native"]["kind"] == "builtin" and sc["native"]["last_status"] == "ok"
            assert sc["vuls"]["accepts_upload"] and sc["vuls"]["last_run_at"]

            # bad input: 400 with a detail, recorded as a failed scan
            r = await c.post("/api/v1/posture/scans/import", headers=A, data={"scanner": "openscap"},
                             files={"file": ("x.xml", b"<html/>", "text/xml")})
            assert r.status_code == 400 and "OpenSCAP" in r.json()["detail"]
            r = await c.post("/api/v1/posture/scans/import", headers=A, data={"scanner": "nope"},
                             files={"file": ("x", b"x", "text/plain")})
            assert r.status_code == 400

            # openssl upgraded -> next native scan marks it fixed (accepted included)
            async with S() as s:
                await s.execute(update(models.PostureInstalledPackage)
                                .where(models.PostureInstalledPackage.source == "openssl")
                                .values(version="3.0.13-1~deb12u1", source_version="3.0.13-1~deb12u1"))
                await s.commit()
            out = await scanning.run_native(WEB01)
            assert out["status"] == "ok" and out["fixed"] == 1 and out["covered"] is True
            d = (await c.get(f"/api/v1/posture/findings/{fid}", headers=A)).json()
            assert d["status"] == "fixed" and d["history"][-1]["event"] == "fixed"

            # an activation with no Debian data must not fix anything by absence
            async with S() as s:
                snap2 = models.FeedSnapshot(version="m2-test-2", generated_at=datetime.now(UTC) - timedelta(days=30),
                                            status="ready", active=False, manifest={}, stats={})
                s.add(snap2)
                await s.flush()
                await s.execute(update(models.FeedSnapshot).values(active=False))
                await s.execute(update(models.FeedSnapshot).where(models.FeedSnapshot.id == snap2.id).values(active=True))
                await s.commit()
            out = await scanning.run_native(WEB01)
            assert out["covered"] is False and out["fixed"] == 0
            sm = (await c.get("/api/v1/posture/findings/summary", headers=A)).json()
            assert sm["feed_stale"] is True and sm["feed_age_days"] == 30
            n = await scanning.rescore_all()
            assert n >= 4

        # every API call audited with a named action
        actions = [a["action"] for a in audit_rows]
        for want in ("posture.native.run", "posture.scan.import", "posture.findings.list", "posture.unmatched.list",
                     "posture.unmatched.assign", "posture.finding.view", "posture.finding.status",
                     "posture.findings.summary", "posture.scanners.list", "posture.native.scan"):
            assert want in actions, want
        assert not [a for a in actions if a.startswith("posture.api.")]
        denied = [a for a in audit_rows if a.get("result") == "denied"]
        assert len(denied) >= 3

        async with S() as s:
            failed = (await s.execute(select(models.PostureScan).where(models.PostureScan.status == "failed"))).scalars().all()
            assert failed and "OpenSCAP" in failed[0].error
        await _pkg("database").engine.dispose()

    asyncio.run(scenario())
