"""Host tree, view-only tree membership and access requests on a REAL PostgreSQL.

identity-service, inventory-service and terminal-service run in-process over ASGI;
every service-to-service call (identity -> inventory membership/accounts,
terminal -> identity authz, inventory -> identity audit, inventory reveal ->
identity resolve) goes through a router transport to the real app, so what is
tested is the real chain, not a stub of it. Schemas come from the real alembic
migrations (identity and inventory, including a down/up of the new revisions).

Opt-in, same variables as test_posture_m2_db.py (POSTURE_TEST_DB_HOST ...).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

if not os.environ.get("POSTURE_TEST_DB_HOST"):
    pytest.skip("POSTURE_TEST_DB_HOST not set (opt-in DB test)", allow_module_level=True)
for _mod in ("fastapi", "sqlalchemy", "asyncpg", "alembic", "httpx", "jwt"):
    pytest.importorskip(_mod)

CORE = Path(__file__).resolve().parent.parent
SVCS = CORE / "services"
SECRET = "tree-test-secret-" + "x" * 32
DBS = {"identity": "tree_identity_test", "inventory": "tree_inventory_test", "terminal": "tree_terminal_test"}
PKGS = {"identity": "tree_idsvc", "inventory": "tree_invsvc", "terminal": "tree_termsvc"}
DIRS = {"identity": "identity-service", "inventory": "inventory-service", "terminal": "terminal-service"}


def _env() -> dict[str, str]:
    return {
        "DB_ENGINE": "postgres", "DB_SSLMODE": "disable", "DB_HOST": os.environ["POSTURE_TEST_DB_HOST"],
        "DB_PORT": os.environ.get("POSTURE_TEST_DB_PORT", "5432"),
        "DB_USER": os.environ.get("POSTURE_TEST_DB_USER", "postgres"),
        "DB_PASSWORD": os.environ.get("POSTURE_TEST_DB_PASSWORD", "postgres"),
        "IDENTITY_DB_NAME": DBS["identity"], "INVENTORY_DB_NAME": DBS["inventory"], "TERMINAL_DB_NAME": DBS["terminal"],
        "SERVICE_JWT_SECRET": SECRET, "JWT_SECRET": SECRET, "API_TOKEN_PEPPER": SECRET,
        "ZA_VAULT_PASSWORD": SECRET, "ZA_VAULT_SALT": "0123456789abcdef0123456789abcdef",
        "IDENTITY_SERVICE_URL": "http://identity", "INVENTORY_SERVICE_URL": "http://inventory",
        "LOG_PATH": os.devnull,
    }


def _load(svc: str):
    alias = PKGS[svc]
    if alias not in sys.modules:
        root = SVCS / DIRS[svc] / "app"
        spec = importlib.util.spec_from_file_location(alias, root / "__init__.py", submodule_search_locations=[str(root)])
        mod = importlib.util.module_from_spec(spec)
        sys.modules[alias] = mod
        spec.loader.exec_module(mod)
    return lambda sub: importlib.import_module(f"{alias}.{sub}")


@pytest.fixture(scope="module")
def world():
    mp = pytest.MonkeyPatch()
    for k, v in _env().items():
        mp.setenv(k, v)
    import asyncpg

    async def recreate():
        conn = await asyncpg.connect(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]),
                                     user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], database="postgres")
        for db in DBS.values():
            await conn.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{db}"')
        await conn.close()
    asyncio.run(recreate())

    def alembic(svc, *args):
        r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=SVCS / DIRS[svc], capture_output=True,
                           text=True, env={**os.environ, "PYTHONPATH": f"{CORE}:{SVCS / DIRS[svc]}"})
        assert r.returncode == 0, (svc, args, r.stderr[-3000:])

    # Real migrations, and the new revisions are reversible.
    for svc, prev in (("identity", "023"), ("inventory", "013")):
        alembic(svc, "upgrade", "head")
        alembic(svc, "downgrade", prev)
        alembic(svc, "upgrade", "head")

    from libs.dbcore import Base

    before = set(Base.metadata.tables)
    mods = {svc: _load(svc) for svc in PKGS}
    try:
        yield SimpleNamespace(mp=mp, **mods)
    finally:
        for alias in PKGS.values():
            for k in [k for k in sys.modules if k == alias or k.startswith(alias + ".")]:
                del sys.modules[k]
        # The services share one declarative Base; leave its metadata as found so a
        # later test that imports these models under another name can still do so.
        for name in set(Base.metadata.tables) - before:
            Base.metadata.remove(Base.metadata.tables[name])
        mp.undo()


class _Router:
    """httpx transport dispatching on the URL host to the in-process apps."""

    def __init__(self, apps):
        import httpx

        self._t = {host: httpx.ASGITransport(app=app) for host, app in apps.items()}

    async def handle_async_request(self, request):
        return await self._t[request.url.host].handle_async_request(request)

    async def aclose(self):
        pass

    # A client closing must not close the shared in-process apps' transports.
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass


def run(world, scenario):
    import httpx
    from fastapi import FastAPI

    I, V, T = world.identity, world.inventory, world.terminal
    id_app, inv_app, term_app = FastAPI(), FastAPI(), FastAPI()
    for r in (I("api.internal").router, I("api.access_requests").router, I("api.host_tree").router,
              I("api.authorizations").router, I("api.my_access").router):
        id_app.include_router(r, prefix="/api/v1")
    for r in (V("api.hosts").router, V("api.credentials").router):
        inv_app.include_router(r, prefix="/api/v1")
    for r in (T("api.sessions").router, T("api.sftp").router):
        term_app.include_router(r, prefix="/api/v1")

    router = _Router({"identity": id_app, "inventory": inv_app, "terminal": term_app})
    real_client = httpx.AsyncClient

    class Routed(real_client):
        def __init__(self, *a, **kw):
            kw.pop("transport", None)
            super().__init__(*a, transport=router, **kw)

    world.mp.setattr(httpx, "AsyncClient", Routed)
    # The real approver-email path runs; only the SMTP hop is captured.
    emails: list[dict] = []

    async def no_mail_cfg(session):
        return None

    async def capture(cfg, to, subject, body):
        emails.append({"to": to, "subject": subject, "body": body})
    world.mp.setattr(I("mailer"), "get_mail_settings", no_mail_cfg)
    world.mp.setattr(I("mailer"), "send_mail_with_settings", capture)

    async def main():
        tdb = T("database")
        async with tdb.engine.begin() as conn:
            await conn.run_sync(lambda c: tdb.Base.metadata.create_all(
                c, tables=[T("models").ZASSHSession.__table__, T("models").ZASessionCommand.__table__]))
        try:
            async with real_client(transport=router, base_url="http://x") as c:
                await scenario(Ctx(world, c, emails))
        finally:
            for svc in (I, V, T):
                await svc("database").engine.dispose()
    asyncio.run(main())


class Ctx:
    AUD = {"identity": "identity-service", "inventory": "inventory-service", "terminal": "terminal-service"}

    def __init__(self, world, client, emails):
        self.w, self.c, self.emails = world, client, emails

    async def call(self, method, svc, path, *, uid="adm1", role="admin", real=None, code=None, params=None, json=None):
        from libs.servicetoken import mint

        r = await self.c.request(method, f"http://{svc}/api/v1{path}", params=params, json=json, headers={
            "X-Service-Token": mint("api-gateway", self.AUD[svc], SECRET), "X-User-Id": uid, "X-User-Name": uid,
            "X-User-Role": role, "X-User-Real-Role": real or role})
        assert code is None or r.status_code == code, (method, path, uid, r.status_code, r.text[:500])
        return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else r


# ── fixtures in the databases ───────────────────────────────────────────────

async def seed_identity(I):
    m = I("models")
    async with I("database").SessionLocal() as s:
        roles = {}
        for name in ("superadmin", "admin", "support", "user"):
            from sqlalchemy import select
            row = (await s.execute(select(m.ZARole).where(m.ZARole.name == name))).scalar_one_or_none()
            if row is None:
                row = m.ZARole(name=name, permissions={})
                s.add(row)
                await s.flush()
            roles[name] = row.id
        for uid, role in (("adm1", "admin"), ("adm2", "admin"), ("sup1", "support"),
                          ("alice", "user"), ("bob", "user"), ("carol", "user")):
            s.add(m.ZAUser(id=uid, username=uid, email=f"{uid}@example.test"))
            await s.flush()
            s.add(m.ZAUserRole(user_id=uid, role_id=roles[role]))
        s.add(m.ZAUserGroup(id="ops", name="ops"))
        await s.flush()
        s.add(m.ZAUserGroupMember(user_id="bob", group_id="ops"))
        s.add(m.ZASetting(key="platform", value={"app_public_url": "https://sr.example"}))
        await s.commit()


async def add_credentials(V, links: dict[str, str]):
    m = V("models")
    async with V("database").SessionLocal() as s:
        for cid, host in links.items():
            s.add(m.ZACredential(id=cid, name=f"{cid}-name", username=cid.replace("c", "u", 1), secret_ciphertext="x"))
            await s.flush()
            s.add(m.ZACredentialHostLink(credential_id=cid, host_id=host))
        await s.commit()


# ── the scenario ─────────────────────────────────────────────────────────────

def test_host_tree_view_grants_and_access_requests(world):
    async def scenario(x: Ctx):
        I, V, T = world.identity, world.inventory, world.terminal
        await seed_identity(I)
        call = x.call

        # ── 1. the tree ────────────────────────────────────────────────────
        g = {}
        for name, parent in (("Prod", None), ("Web", "Prod"), ("WebEU", "Web"), ("Dev", None)):
            g[name] = (await call("POST", "inventory", "/host-groups", code=201,
                                  json={"name": name, "parent_id": g.get(parent)}))["id"]
        listed = {r["name"]: r["parent_id"] for r in await call("GET", "inventory", "/host-groups", code=200)}
        assert listed == {"Prod": None, "Web": g["Prod"], "WebEU": g["Web"], "Dev": None}
        assert (await call("GET", "inventory", f"/host-groups/{g['WebEU']}", code=200))["parent_id"] == g["Web"]

        await call("POST", "inventory", "/host-groups", uid="alice", role="user", code=403, json={"name": "nope"})
        r = await call("PUT", "inventory", f"/host-groups/{g['Prod']}", code=400, json={"parent_id": g["WebEU"]})
        assert "cycle" in r["detail"]
        await call("PUT", "inventory", f"/host-groups/{g['Prod']}", code=400, json={"parent_id": g["Prod"]})
        await call("POST", "inventory", "/host-groups", code=400, json={"name": "orphan", "parent_id": "missing"})
        # renaming does not move
        await call("PUT", "inventory", f"/host-groups/{g['Web']}", code=200, json={"description": "web tier"})
        assert (await call("GET", "inventory", f"/host-groups/{g['Web']}"))["parent_id"] == g["Prod"]

        chain = [None]
        for i in range(1, 9):
            chain.append((await call("POST", "inventory", "/host-groups", code=201,
                                     json={"name": f"L{i}", "parent_id": chain[-1]}))["id"])
        r = await call("POST", "inventory", "/host-groups", code=400, json={"name": "L9", "parent_id": chain[8]})
        assert "8 levels" in r["detail"]
        await call("PUT", "inventory", f"/host-groups/{chain[1]}", code=400, json={"parent_id": g["Dev"]})

        r = await call("DELETE", "inventory", f"/host-groups/{g['Prod']}", code=409)
        assert "child groups" in r["detail"]
        await call("DELETE", "inventory", f"/host-groups/{chain[8]}", code=204)

        h = {}
        for name, grp in (("web1", "WebEU"), ("root1", "Prod"), ("dev1", "Dev")):
            h[name] = (await call("POST", "inventory", "/hosts", code=201,
                                  json={"name": name, "ip": "10.0.0.1", "group_ids": [g[grp]]}))["id"]
        await add_credentials(V, {"cweb": h["web1"], "cweb2": h["web1"], "cdev": h["dev1"]})

        async def host_ids(uid, **params):
            return await call("GET", "identity", "/internal/authz/host-ids", params={"user_id": uid, **params}, code=200)

        async def resolve(uid, host):
            return await call("GET", "identity", "/internal/authz/resolve", params={"user_id": uid, "host_id": host}, code=200)

        async def connect(uid, host, cred=None, code=None):
            return await call("POST", "terminal", "/ssh/sessions", uid=uid, role="user", code=code,
                              json={"host_id": host, "credential_id": cred})

        async def can_reveal(uid, cred):
            inv = V("api.credentials")
            async with V("database").SessionLocal() as s:
                return await inv._credential_authorized_for_reveal(s, cred, uid, V("config").get_settings())

        # ── 2. a rule on a group covers its descendants: visibility AND login ──
        rule = await call("POST", "identity", "/authorizations", role="superadmin", real="superadmin", code=201, json={
            "name": "alice prod", "user_ids": ["alice"], "host_group_ids": [g["Prod"]],
            "credential_ids": ["cweb"], "actions": ["ssh"]})
        assert rule["status"] == "active"
        seen = set((await host_ids("alice", strict="true"))["host_ids"])
        assert h["web1"] in seen and h["root1"] in seen and h["dev1"] not in seen
        assert set((await host_ids("alice", include_view="true"))["host_ids"]) == seen
        res = await resolve("alice", h["web1"])
        assert res["credential_ids"] == ["cweb"] and res["actions"] == ["ssh"]
        assert (await connect("alice", h["web1"], code=201))["host_id"] == h["web1"]

        # ── 3. tree view membership is view only ─────────────────────────────
        await call("POST", "identity", "/authorizations/host-group-viewers", uid="sup1", role="admin", real="support",
                   code=403, json={"host_group_id": g["Web"], "user_group_ids": ["ops"]})
        await call("POST", "identity", "/authorizations/host-group-viewers", code=404,
                   json={"host_group_id": "no-such-group", "user_ids": ["bob"]})
        added = await call("POST", "identity", "/authorizations/host-group-viewers", code=201,
                           json={"host_group_id": g["Web"], "user_group_ids": ["ops"]})
        assert len(added) == 1 and added[0]["user_group_id"] == "ops"
        again = await call("POST", "identity", "/authorizations/host-group-viewers", code=201,
                           json={"host_group_id": g["Web"], "user_group_ids": ["ops"]})
        assert again == [], "adding an existing viewer is a no-op"

        assert h["web1"] not in (await host_ids("bob"))["host_ids"], "view grants are opt-in"
        vis = await host_ids("bob", include_view="true")
        assert h["web1"] in vis["host_ids"] and vis["view_host_ids"] == [h["web1"]]
        assert h["root1"] not in vis["host_ids"], "viewing Web does not show its parent's hosts"
        assert h["web1"] not in (await host_ids("bob", strict="true", include_view="true"))["host_ids"]
        res = await resolve("bob", h["web1"])
        assert res == {"credential_id": None, "credential_ids": [], "actions": []}
        r = await connect("bob", h["web1"], "cweb", code=403)
        assert "not authorized" in r["detail"]
        assert await call("GET", "terminal", "/ssh/hosts", uid="bob", role="user", code=200) == []
        creds = await call("GET", "terminal", "/ssh/credentials", uid="bob", role="user", params={"host_id": h["web1"]}, code=200)
        assert creds == {"credentials": [], "manual_allowed": False}
        assert await can_reveal("bob", "cweb") is False
        # SFTP on a session that exists (e.g. left from earlier access) — still refused.
        tm = T("models")
        async with T("database").SessionLocal() as s:
            s.add(tm.ZASSHSession(id="sess-bob", user_id="bob", username="bob", host_id=h["web1"], host_name="web1",
                                  credential_id="cweb", status="active", client_ip=""))
            await s.commit()
        r = await call("GET", "terminal", "/sftp/sess-bob/list", uid="bob", role="user", params={"path": "/"}, code=403)
        assert "no active authorization" in r["detail"]

        # ── 4. access requests ─────────────────────────────────────────────
        opts = await call("GET", "identity", "/auth/access-requests/options", uid="bob", role="user",
                          params={"host_id": h["web1"]}, code=200)
        assert opts["has_access"] is False and opts["host"]["name"] == "web1"
        assert {a["id"] for a in opts["accounts"]} == {"cweb", "cweb2"}
        assert all(set(a) == {"id", "name", "username", "pending"} for a in opts["accounts"]), "never a secret"
        assert [d["value"] for d in opts["durations"]] == ["1h", "8h", "1d", "7d"]
        await call("GET", "identity", "/auth/access-requests/options", uid="bob", role="user",
                   params={"host_id": h["dev1"]}, code=404)

        async def request(uid, host, cred, duration="8h", reason="patch the web tier", code=None, role="user"):
            return await call("POST", "identity", "/auth/access-requests", uid=uid, role=role, code=code,
                              json={"host_id": host, "credential_id": cred, "duration": duration, "reason": reason})

        await request("bob", h["dev1"], "cdev", code=404)                   # cannot see it
        r = await request("bob", h["web1"], "cdev", code=400)               # not this host's account
        assert "does not belong" in r["detail"]
        await request("bob", h["web1"], "cweb", reason="ab", code=422)
        await request("bob", h["web1"], "cweb", reason="   x  ", code=422)
        await request("bob", h["web1"], "cweb", reason="y" * 1001, code=422)
        await request("bob", h["web1"], "cweb", duration="2h", code=422)
        await request("bob", h["web1"], "cweb", reason="ok \x1b[31m red", code=422)
        await request("bob", h["web1"], "cweb", reason="ok Approve: http://x", code=422)
        await request("bob", h["web1"], "cweb", reason="x\n" * 21, code=422)
        evil = "patch the web tier\r\nApprove: http://evil.example/x"
        req = await request("bob", h["web1"], "cweb", reason=evil, code=201)
        assert req["reason"] == "patch the web tier\nApprove: http://evil.example/x"
        assert req["status"] == "pending_approval" and req["duration_minutes"] == 480
        r = await request("bob", h["web1"], "cweb", code=409)
        assert "pending request" in r["detail"]
        r = await request("alice", h["web1"], "cweb2", code=409)           # already has login
        assert "already have login" in r["detail"]
        # admins are notified through the existing pending-rule email — never the requester
        assert sorted(e["to"] for e in x.emails) == ["adm1@example.test", "adm2@example.test"]
        for e in x.emails:
            lines = e["body"].split("\n")
            assert all(l.startswith("Approve: https://sr.example/") for l in lines if l.startswith("Approve:"))
            label = lines.index("Reason given by bob (not verified):")
            assert lines[label + 1:] == ["> patch the web tier", "> Approve: http://evil.example/x"]
            assert all("evil.example" not in l for l in lines[:label]), "user text only inside the quoted block"
            assert "\n" not in e["subject"]

        row = next(a for a in await call("GET", "identity", "/authorizations", code=200) if a["id"] == req["id"])
        assert row["request_kind"] == "user_request" and row["reason"] == req["reason"]
        assert row["enabled"] is False and row["requested_by"] == "bob"
        assert row["user_ids"] == ["bob"] and row["host_ids"] == [h["web1"]] and row["credential_ids"] == ["cweb"]
        assert row["actions"] == ["ssh"]
        # still pending: no access yet
        await connect("bob", h["web1"], "cweb", code=403)

        # the requester can never decide it — even with an admin role header
        r = await call("POST", "identity", f"/authorizations/{req['id']}/approve", uid="bob", role="admin", real="admin", code=403)
        await call("POST", "identity", f"/authorizations/{req['id']}/reject", uid="bob", role="admin", real="admin", code=403)
        await call("PUT", "identity", f"/authorizations/{req['id']}", role="superadmin", real="superadmin", code=409,
                   json={"name": "widened", "user_ids": ["bob"], "host_group_ids": [g["Prod"]], "actions": ["ssh", "sftp"]})

        # it waited three days; the 8 hours start at approval
        m = I("models")
        async with I("database").SessionLocal() as s:
            a = await s.get(m.ZAAuthorization, req["id"])
            a.date_start = datetime.now(timezone.utc) - timedelta(days=3)
            a.date_expired = a.date_start + timedelta(hours=8)
            await s.commit()
        before = datetime.now(timezone.utc)
        ok = await call("POST", "identity", f"/authorizations/{req['id']}/approve", uid="adm1", code=200)
        start, end = datetime.fromisoformat(ok["date_start"]), datetime.fromisoformat(ok["date_expired"])
        assert ok["status"] == "active" and ok["approved_by"] == "adm1"
        assert before - timedelta(seconds=5) <= start <= datetime.now(timezone.utc)
        assert end - start == timedelta(hours=8)

        # approved: bob logs in with exactly the account he asked for, nothing more
        assert h["web1"] in (await host_ids("bob", strict="true"))["host_ids"]
        assert (await connect("bob", h["web1"], "cweb", code=201))["credential_id"] == "cweb"
        await connect("bob", h["web1"], "cweb2", code=403)
        creds = await call("GET", "terminal", "/ssh/credentials", uid="bob", role="user", params={"host_id": h["web1"]}, code=200)
        assert [c["id"] for c in creds["credentials"]] == ["cweb"]
        r = await call("GET", "terminal", "/sftp/sess-bob/list", uid="bob", role="user", params={"path": "/"}, code=403)
        assert "sftp action not permitted" in r["detail"]
        assert await can_reveal("bob", "cweb") is False
        opts = await call("GET", "identity", "/auth/access-requests/options", uid="bob", role="user",
                          params={"host_id": h["web1"]}, code=200)
        assert opts["has_access"] is True

        # expiry ends it everywhere, including an SFTP call on the still-open session
        async with I("database").SessionLocal() as s:
            a = await s.get(m.ZAAuthorization, req["id"])
            a.date_expired = datetime.now(timezone.utc) - timedelta(seconds=1)
            await s.commit()
        assert h["web1"] not in (await host_ids("bob", strict="true"))["host_ids"]
        await connect("bob", h["web1"], "cweb", code=403)
        r = await call("GET", "terminal", "/sftp/sess-bob/list", uid="bob", role="user", params={"path": "/"}, code=403)
        assert "no active authorization" in r["detail"]
        mine = (await call("GET", "identity", "/auth/access-requests", uid="bob", role="user", code=200))["items"]
        assert [i["status"] for i in mine] == ["expired"]

        # cancel: own pending only
        c2 = await request("bob", h["web1"], "cweb2", duration="1h", code=201)
        await call("POST", "identity", f"/auth/access-requests/{c2['id']}/cancel", uid="carol", role="user", code=404)
        await call("POST", "identity", f"/auth/access-requests/{rule['id']}/cancel", uid="alice", role="user", code=404)
        assert (await call("POST", "identity", f"/auth/access-requests/{c2['id']}/cancel", uid="bob", role="user",
                           code=200))["status"] == "cancelled"
        await call("POST", "identity", f"/auth/access-requests/{c2['id']}/cancel", uid="bob", role="user", code=409)
        await call("POST", "identity", f"/authorizations/{c2['id']}/approve", uid="adm1", code=400)

        # reject
        c3 = await request("bob", h["web1"], "cweb2", duration="1d", code=201)
        assert (await call("POST", "identity", f"/authorizations/{c3['id']}/reject", uid="adm2", code=200))["status"] == "rejected"
        await connect("bob", h["web1"], "cweb2", code=403)

        mine = (await call("GET", "identity", "/auth/access-requests", uid="bob", role="user", code=200))["items"]
        assert sorted(i["status"] for i in mine) == ["cancelled", "expired", "rejected"]
        assert (await call("GET", "identity", "/auth/access-requests", uid="carol", role="user", code=200))["items"] == []

        # removing the viewer removes the visibility
        await call("DELETE", "identity", f"/authorizations/host-group-viewers/{added[0]['id']}", code=204)
        assert h["web1"] not in (await host_ids("bob", include_view="true"))["host_ids"]
        await call("GET", "identity", "/auth/access-requests/options", uid="bob", role="user",
                   params={"host_id": h["web1"]}, code=404)

        # every step is in the audit chain
        from sqlalchemy import select
        async with I("database").SessionLocal() as s:
            acts = [a for (a,) in (await s.execute(select(m.ZAAuditLog.action))).all()]
        for want in ("access_request.create", "access_request.cancel", "authorization.approve", "authorization.reject",
                     "host_group_viewer.add", "host_group_viewer.remove", "host_group.create", "host_group.update",
                     "host_group.delete"):
            assert want in acts, want

    run(world, scenario)
