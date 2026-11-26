"""Zabbix calls around a patch rollout: idempotent, and limited to our own maintenances."""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
CORE = Path(__file__).resolve().parent.parent
SVC = CORE / "services/zabbix-integration-service"


@pytest.fixture(scope="module")
def mod():
    monkeypatch = pytest.MonkeyPatch()
    for k, v in {"DB_PASSWORD": "x", "SERVICE_JWT_SECRET": "x" * 40, "LOG_PATH": os.devnull,
                 "ZABBIX_WEBHOOK_HMAC_SECRET": "x" * 40}.items():
        monkeypatch.setenv(k, v)
    saved = {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}
    for k in saved:
        del sys.modules[k]
    monkeypatch.syspath_prepend(str(SVC))
    try:
        yield importlib.import_module("app.api.zabbix_internal")
    except ModuleNotFoundError as exc:
        pytest.skip(f"service dependencies not installed ({exc.name})")
    finally:
        for k in [k for k in sys.modules if k == "app" or k.startswith("app.")]:
            del sys.modules[k]
        sys.modules.update(saved)
        monkeypatch.undo()


class Zbx:
    def __init__(self):
        self.calls, self.maint = [], {}

    async def rpc(self, method, params):
        self.calls.append((method, params))
        if method == "maintenance.get":
            if "filter" in params:
                return [{"maintenanceid": k} for k, n in self.maint.items() if n in params["filter"]["name"]]
            return [{"maintenanceid": k, "name": self.maint[k]} for k in params["maintenanceids"] if k in self.maint]
        if method == "maintenance.create":
            self.maint["7"] = params["name"]
            return {"maintenanceids": ["7"]}
        if method == "maintenance.delete":
            for k in params:
                self.maint.pop(k)
            return {}
        return [{"eventid": "1", "objectid": "22", "name": "down", "severity": "4"}]


@pytest.fixture
def zbx(mod, monkeypatch):
    z = Zbx()

    async def hostid(_):
        return "10084"
    monkeypatch.setattr(mod, "_rpc", z.rpc)
    monkeypatch.setattr(mod, "_zabbix_hostid", hostid)
    return z


def test_maintenance_is_idempotent_and_keeps_collecting(mod, zbx):
    req = mod.MaintenanceCreate(host_id="h", reference="abc-1", minutes=60)
    a = asyncio.run(mod.create_maintenance(req))
    b = asyncio.run(mod.create_maintenance(req))
    assert a["maintenance_id"] == b["maintenance_id"] == "7" and b["reused"]
    created = [p for m, p in zbx.calls if m == "maintenance.create"]
    assert len(created) == 1 and created[0]["maintenance_type"] == 0
    assert created[0]["hosts"] == [{"hostid": "10084"}] and created[0]["active_till"] - created[0]["active_since"] == 3600


def test_only_our_own_maintenance_can_be_ended(mod, zbx):
    zbx.maint["9"] = "Quarterly datacentre work"
    with pytest.raises(mod.HTTPException) as e:
        asyncio.run(mod.end_maintenance("9"))
    assert e.value.status_code == 403 and "9" in zbx.maint
    zbx.maint["8"] = mod.NAME_PREFIX + "x"
    assert asyncio.run(mod.end_maintenance("8"))["ended"] and "8" not in zbx.maint
    assert asyncio.run(mod.end_maintenance("404"))["ended"] is False
    with pytest.raises(mod.HTTPException):
        asyncio.run(mod.end_maintenance("1 or 1"))


def test_problems_shape(mod, zbx):
    out = asyncio.run(mod.open_problems(host_id="h", min_severity=3))
    assert out == {"problems": [{"event_id": "1", "trigger_id": "22", "name": "down", "severity": 4}]}
    assert zbx.calls[-1][1]["severities"] == [3, 4, 5]


def test_unconfigured_zabbix_is_reported_not_ignored(mod, monkeypatch):
    async def none(_):
        return None, None
    monkeypatch.setattr(mod, "_resolve_zabbix_creds", none)
    with pytest.raises(mod.HTTPException) as e:
        asyncio.run(mod._rpc("x", {}))
    assert e.value.status_code == 424
