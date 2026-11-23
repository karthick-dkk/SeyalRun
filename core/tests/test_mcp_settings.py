"""MCP settings (Settings card on the MCP page) — every enforcement path.

  * defaults == behaviour before the settings existed (upgrade changes nothing);
  * MCP turned off -> mcp-server refuses initialize / tools/list / tools/call
    with a JSON-RPC error and never calls the gateway; the gateway refuses an
    MCP-tagged call too;
  * read-only -> the three write tools are neither listed nor callable;
  * per-token rate limit, run through the gateway's REAL sliding-window limiter
    (rate_limit.check_rate_limit) over an in-memory Redis;
  * playbook allowlist binds every PAT, MCP or not, and never a session caller;
  * a failed settings read keeps the last known value, else the defaults;
  * every MCP tool call produces one audit row that the audit API's source=mcp
    filter actually selects.

Service modules are loaded by file path (each service's package is named `app`).
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

from libs import mcpsettings

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mcp = _load("services/mcp-server/app/rpc.py", "mcp_rpc_settings")
gate = _load("services/api-gateway/app/mcp_gate.py", "gw_mcp_gate")

WRITE_TOOLS = {"create_host", "run_automation", "ack_notification"}
PAT = "sr_testtoken"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def policy(monkeypatch):
    """Drive mcp-server's policy source; records the gateway calls it would make."""
    state = {"policy": None, "fetches": 0, "tool_calls": []}

    async def fake_fetch(pat):
        state["fetches"] += 1
        p = state["policy"]
        return None if p is None else mcpsettings.public_policy(p)

    async def fake_call(tool, args, pat):
        state["tool_calls"].append(tool["name"])
        return {"content": [{"type": "text", "text": "{}"}], "isError": False}

    monkeypatch.setattr(mcp, "_fetch_policy", fake_fetch)
    monkeypatch.setattr(mcp, "_call_tool", fake_call)
    monkeypatch.setattr(mcp, "POLICY_CACHE_SECONDS", 0.0)
    mcp._reset_policy_cache()
    yield state
    mcp._reset_policy_cache()


def _rpc(method, params=None, pat=PAT, rid=1):
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return _run(mcp.handle(msg, pat))


# ── defaults ─────────────────────────────────────────────────────────────────

def test_defaults_match_pre_settings_behaviour():
    d = mcpsettings.defaults()
    assert d == {"enabled": True, "allow_write_tools": True, "calls_per_minute": 60,
                 "agent_template_ids": None}
    # no allowlist configured = any template runnable, exactly as before
    assert mcpsettings.template_allowed(d, "any-template-id")


@pytest.mark.parametrize("raw", [None, "garbage", [], {}, {"enabled": "no"},
                                 {"calls_per_minute": 45}, {"calls_per_minute": True},
                                 {"agent_template_ids": "T1"}])
def test_malformed_settings_fall_back_to_defaults_not_inventions(raw):
    assert mcpsettings.normalize(raw) == mcpsettings.defaults()


def test_normalize_keeps_valid_values_and_cleans_ids():
    s = mcpsettings.normalize({"enabled": False, "allow_write_tools": False, "calls_per_minute": 120,
                               "agent_template_ids": ["T1", "T1", " T2 ", "../x", 7, ""]})
    assert s == {"enabled": False, "allow_write_tools": False, "calls_per_minute": 120,
                 "agent_template_ids": ["T1", "T2"]}
    # an EMPTY list is a real choice ("no playbooks"), distinct from None ("any")
    assert mcpsettings.normalize({"agent_template_ids": []})["agent_template_ids"] == []
    assert not mcpsettings.template_allowed({"agent_template_ids": []}, "T1")


def test_policy_endpoint_does_not_disclose_the_allowlist():
    p = mcpsettings.public_policy({"agent_template_ids": ["secret-template"]})
    assert set(p) == {"enabled", "allow_write_tools", "calls_per_minute"}


def test_identity_put_accepts_exactly_the_three_rate_choices():
    src = (SERVICES / "identity-service/app/api/settings.py").read_text()
    assert "CALLS_PER_MINUTE_CHOICES" in src and 'MCP_KEY = "mcp"' in src
    assert mcpsettings.CALLS_PER_MINUTE_CHOICES == (30, 60, 120)


# ── mcp-server: MCP turned off ───────────────────────────────────────────────

def test_disabled_refuses_initialize_list_and_call_without_touching_the_gateway(policy):
    policy["policy"] = {"enabled": False}
    for method, params in (("initialize", {}), ("tools/list", None), ("ping", None),
                           ("resources/list", None),
                           ("tools/call", {"name": "list_hosts", "arguments": {}}),
                           ("tools/call", {"name": "whoami", "arguments": {}})):
        r = _rpc(method, params)
        assert "result" not in r, method
        assert r["error"]["code"] == mcp.ERR_POLICY
        assert "turned off" in r["error"]["message"] and "REST API" in r["error"]["message"]
    assert policy["tool_calls"] == []


def test_enabled_by_default_when_policy_cannot_be_read(policy):
    policy["policy"] = None  # gateway unreachable / 401 / garbage, nothing known yet
    r = _rpc("tools/call", {"name": "list_hosts", "arguments": {}})
    assert r["result"]["isError"] is False
    assert policy["tool_calls"] == ["list_hosts"]


def test_failed_read_keeps_last_known_value(policy):
    policy["policy"] = {"enabled": False}
    assert "error" in _rpc("initialize", {})
    policy["policy"] = None  # next refresh fails
    r = _rpc("initialize", {})
    assert r["error"]["code"] == mcp.ERR_POLICY, "a read failure must not silently re-enable MCP"
    policy["policy"] = {"enabled": True}
    assert "result" in _rpc("initialize", {})


def test_toggle_takes_effect_on_the_next_refresh(policy):
    policy["policy"] = {"enabled": True}
    assert "result" in _rpc("initialize", {})
    policy["policy"] = {"enabled": False}
    assert "error" in _rpc("initialize", {})
    assert policy["fetches"] == 2


def test_cache_windows_keep_a_toggle_within_about_ten_seconds():
    gw = (SERVICES / "api-gateway/app/platform_settings.py").read_text()
    gw_ttl = float(re.search(r"^MCP_CACHE_SECONDS\s*=\s*([\d.]+)", gw, re.M).group(1))
    assert mcp.POLICY_CACHE_SECONDS + gw_ttl <= 10.0


def test_notifications_are_never_answered_even_when_disabled(policy):
    policy["policy"] = {"enabled": False}
    assert _run(mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, PAT)) is None


# ── mcp-server: read-only ────────────────────────────────────────────────────

def test_write_tools_are_exactly_the_non_get_tools():
    assert {t["name"] for t in mcp.TOOLS if t["kind"] == "write"} == WRITE_TOOLS


def test_read_only_hides_write_tools(policy):
    policy["policy"] = {"allow_write_tools": False}
    names = {t["name"] for t in _rpc("tools/list")["result"]["tools"]}
    assert names and not (names & WRITE_TOOLS)
    assert {"whoami", "list_hosts", "get_posture_summary", "list_findings"} <= names
    policy["policy"] = {"allow_write_tools": True}
    assert WRITE_TOOLS <= {t["name"] for t in _rpc("tools/list")["result"]["tools"]}


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_read_only_refuses_write_tool_calls(policy, tool):
    policy["policy"] = {"allow_write_tools": False}
    r = _rpc("tools/call", {"name": tool, "arguments": {"template_id": "T1", "notification_id": "n1"}})
    assert r["error"]["code"] == mcp.ERR_POLICY and "read-only" in r["error"]["message"]
    assert policy["tool_calls"] == []


def test_read_only_still_allows_read_tools(policy):
    policy["policy"] = {"allow_write_tools": False}
    r = _rpc("tools/call", {"name": "list_findings", "arguments": {"severity": "critical"}})
    assert r["result"]["isError"] is False and policy["tool_calls"] == ["list_findings"]


# ── gateway: MCP-tagged calls ────────────────────────────────────────────────

def _identity(token_id="tok-a", is_pat=True):
    return {"user_id": "u1", "username": "alice", "is_pat": is_pat,
            "token_id": token_id, "token_name": f"name-{token_id}"}


async def _always(key, limit, window):
    return True


def test_gateway_refuses_mcp_call_when_disabled():
    d = _run(gate.check_mcp_call(_identity(), "GET", {"enabled": False}, _always))
    assert d[0] == 403 and "turned off" in d[1]


def test_gateway_read_only_refuses_writes_only():
    s = {"allow_write_tools": False}
    assert _run(gate.check_mcp_call(_identity(), "POST", s, _always))[0] == 403
    assert _run(gate.check_mcp_call(_identity(), "GET", s, _always)) is None


def test_tool_header_parsing():
    assert gate.tool_from_headers({}) is None
    assert gate.tool_from_headers({"x-mcp-tool": "list_hosts"}) == "list_hosts"
    assert gate.tool_from_headers({"x-mcp-tool": "x; drop table"}) == "unknown"


# ── gateway: per-token rate limit through the real limiter ──────────────────

class _FakePipe:
    def __init__(self, store):
        self.store, self.ops = store, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def zremrangebyscore(self, key, lo, hi):
        self.ops.append(("zrem", key, lo, hi))

    def zadd(self, key, mapping):
        self.ops.append(("zadd", key, mapping))

    def zcard(self, key):
        self.ops.append(("zcard", key))

    def expire(self, key, secs):
        self.ops.append(("expire", key))

    async def execute(self):
        out = []
        for op in self.ops:
            z = self.store.setdefault(op[1], {})
            if op[0] == "zrem":
                for m in [m for m, sc in z.items() if op[2] <= sc <= op[3]]:
                    del z[m]
                out.append(0)
            elif op[0] == "zadd":
                z.update(op[2])
                out.append(1)
            elif op[0] == "zcard":
                out.append(len(z))
            else:
                out.append(True)
        return out


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def pipeline(self, transaction=True):
        return _FakePipe(self.store)


@pytest.fixture
def limiter(monkeypatch):
    fake_redis_mod = types.ModuleType("redis")
    fake_async = types.ModuleType("redis.asyncio")

    class RedisError(Exception):
        pass

    fake_async.RedisError = RedisError
    fake_async.Redis = object
    fake_redis_mod.asyncio = fake_async
    monkeypatch.setitem(sys.modules, "redis", fake_redis_mod)
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_async)
    rl = _load("services/api-gateway/app/rate_limit.py", "gw_rate_limit")
    clock = {"t": 1_000_000.0}

    def tick():
        clock["t"] += 0.01
        return clock["t"]

    monkeypatch.setattr(rl.time, "time", tick)
    r = _FakeRedis()

    async def lim(key, limit, window):
        return await rl.check_rate_limit(r, key, limit, window)

    lim.clock, lim.redis = clock, r
    return lim


def _calls(identity, n, settings, lim):
    return [_run(gate.check_mcp_call(identity, "GET", settings, lim)) for _ in range(n)]


def test_rate_limit_default_60_per_minute_per_token(limiter):
    out = _calls(_identity("tok-a"), 61, {}, limiter)
    assert all(d is None for d in out[:60])
    assert out[60][0] == 429 and "60 calls per minute" in out[60][1]
    # keyed by token: another token of the same owner has its own budget
    assert _calls(_identity("tok-b"), 1, {}, limiter) == [None]
    assert any(k.endswith("mcp:tok-a") for k in limiter.redis.store)


@pytest.mark.parametrize("cpm", [30, 120])
def test_rate_limit_follows_the_setting(limiter, cpm):
    out = _calls(_identity("tok-" + str(cpm)), cpm + 1, {"calls_per_minute": cpm}, limiter)
    assert all(d is None for d in out[:cpm]) and out[cpm][0] == 429


def test_rate_limit_window_slides(limiter):
    ident = _identity("tok-slide")
    assert _calls(ident, 31, {"calls_per_minute": 30}, limiter)[-1][0] == 429
    limiter.clock["t"] += 61  # a minute later the old calls have left the window
    assert _calls(ident, 1, {"calls_per_minute": 30}, limiter) == [None]


# ── gateway: playbook allowlist (every PAT) ─────────────────────────────────

def test_allowlist_unset_allows_any_template():
    assert gate.check_run_allowlist(_identity(), "POST", "job-templates/T9/run", {}) is None


def test_allowlist_refuses_unlisted_template_for_any_pat():
    s = {"agent_template_ids": ["T1"]}
    d = gate.check_run_allowlist(_identity(), "POST", "job-templates/T9/run", s)
    assert d[0] == 403 and "list agents may run" in d[1]
    assert gate.check_run_allowlist(_identity(), "POST", "job-templates/T1/run", s) is None
    assert gate.check_run_allowlist(_identity(), "POST", "job-templates/T9/run/", s)[0] == 403


def test_allowlist_never_binds_session_callers_or_other_routes():
    s = {"agent_template_ids": []}
    assert gate.check_run_allowlist(_identity(is_pat=False), "POST", "job-templates/T9/run", s) is None
    assert gate.check_run_allowlist(_identity(), "PUT", "job-templates/T9", s) is None
    assert gate.check_run_allowlist(_identity(), "GET", "job-templates", s) is None


def test_gateway_wires_the_checks_into_the_request_path():
    """Supplement to the behavioural tests above: the catch-all really calls them,
    after the scope check and before proxying."""
    src = (SERVICES / "api-gateway/app/main.py").read_text()
    body = src.split("async def _gateway(", 1)[1]
    i_scope = body.index("token scope does not permit")
    i_allow = body.index("check_run_allowlist")
    i_mcp = body.index("_enforce_mcp_call(identity, mcp_tool")
    i_proxy = body.index("await proxy(request")
    assert i_scope < i_allow < i_proxy and i_scope < i_mcp < i_proxy
    assert "_enforce_mcp_call(identity, mcp_tool" in src.split("async def auth_session", 1)[1].split("@app.", 1)[0]


# ── audit attribution + source filter ───────────────────────────────────────

def test_audit_entry_attributes_call_to_token_and_owner():
    e = gate.audit_entry(_identity("tok-a"), "run_automation", "post", "job-templates/T1/run",
                         403, "forbidden: token scope does not permit this action", "10.0.0.5")
    assert e["action"] == "mcp.tool_call" and e["result"] == "failure"
    assert e["user_id"] == "u1" and e["username"] == "alice"
    assert e["resource_id"] == "run_automation"
    assert e["details"]["token_name"] == "name-tok-a" and e["details"]["status"] == 403
    assert "token scope" in e["details"]["reason"]
    ok = gate.audit_entry(_identity(), "list_hosts", "GET", "hosts", 200, None, "")
    assert ok["result"] == "success" and "reason" not in ok["details"]


def test_audit_source_filter_selects_mcp_rows():
    src = (SERVICES / "identity-service/app/api/audit.py").read_text()
    sources = ast.literal_eval(re.search(r"^AUDIT_SOURCES\s*=\s*(\{.*?\})", src, re.M).group(1))
    assert gate.AUDIT_ACTION.startswith(sources["mcp"])
    # and nothing else the platform writes is swept in by it
    others = set(re.findall(r'action="([a-z_.]+)"', "".join(
        p.read_text() for p in SERVICES.glob("*/app/**/*.py"))))
    assert others, "found no audit actions to compare against"
    assert not [a for a in others if a.startswith(sources["mcp"])]


def test_token_verify_carries_token_identity():
    src = (SERVICES / "identity-service/app/api/internal.py").read_text()
    assert "token_id=token.id, token_name=token.name" in src
    gw = (SERVICES / "api-gateway/app/security.py").read_text()
    assert '"token_id": data.get("token_id")' in gw


# ── run_automation argument mapping matches automation-service ──────────────

def test_run_automation_body_keys_are_runpayload_fields():
    src = (SERVICES / "automation-service/app/api/job_templates.py").read_text()
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "RunPayload")
    fields = {n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)}
    tool = mcp._TOOLS_BY_NAME["run_automation"]
    _, _, _, body = mcp._build_request(tool, {"template_id": "T", "host_ids": ["h"], "extra_vars": {"a": 1}})
    assert body and set(body) <= fields, (body, fields)


# ── security review fixes: fail closed + every run-starting route ──────────────
_PAT = {"is_pat": True, "token_id": "t1", "user_id": "u1"}


def test_unloaded_settings_fail_closed():
    async def _ok(*_a):
        return True
    assert _run(gate.check_mcp_call(_PAT, "GET", None, _ok))[0] == 503
    assert gate.check_run_allowlist(_PAT, "POST", "job-templates/abc/run", None)[0] == 503
    # A session caller and a non-run request are unaffected.
    assert gate.check_run_allowlist({"is_pat": False}, "POST", "job-templates/abc/run", None) is None
    assert gate.check_run_allowlist(_PAT, "GET", "job-templates", None) is None


@pytest.mark.parametrize("method,path", [
    ("POST", "job-runs/r1/rerun"), ("POST", "job-runs/r1/approve"), ("POST", "triggers/manual"),
    ("POST", "schedules"), ("PUT", "schedules/s1"),
    ("POST", "trigger-bindings"), ("PUT", "trigger-bindings/b1"),
])
def test_rerun_and_schedules_held_to_the_list(method, path):
    listed = mcpsettings.normalize({"agent_template_ids": ["abc"]})
    unlisted = mcpsettings.normalize({"agent_template_ids": None})
    assert gate.starts_run(method, path)
    assert gate.check_run_allowlist(_PAT, method, path, listed)[0] == 403
    assert gate.check_run_allowlist(_PAT, method, path, unlisted) is None
    assert gate.check_run_allowlist({"is_pat": False}, method, path, listed) is None


def test_listed_template_still_runs():
    listed = mcpsettings.normalize({"agent_template_ids": ["abc"]})
    assert gate.check_run_allowlist(_PAT, "POST", "job-templates/abc/run", listed) is None
    assert gate.check_run_allowlist(_PAT, "POST", "job-templates/xyz/run", listed)[0] == 403


def test_reject_and_reads_are_not_run_starts():
    assert not gate.starts_run("POST", "job-runs/r1/reject")
    assert not gate.starts_run("GET", "trigger-bindings")
