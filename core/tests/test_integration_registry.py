"""The Integration page's rules, executed rather than asserted from source.

Three properties matter here, and each one is a way this feature could quietly
become a security hole rather than a settings page:

1. The two axes that have no safe off state must have no off switch. Disabling
   the vault's key provider does not "turn off a feature", it makes every stored
   credential unreadable — so the refusal is a rule, not a UI convention.
2. `integrations` is platform configuration: superadmin-only, and never
   grantable to an API token. An agent that could disable the Zabbix SSO
   provider, or re-enable an executor an admin turned off, would be
   administering access.
3. A disabled executor must stay disabled when identity-service is briefly
   unreachable. "Could not ask" must never be read as "nothing is disabled",
   because that failure mode silently re-enables exactly what an admin switched
   off — and it would do so at the moment the platform is already unhealthy.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from libs.apiscopes import AGENT_GRANTABLE, required_scope, validate_agent_scopes  # noqa: E402
from libs.pluginbase import AXES, describe_plugins  # noqa: E402
from libs.rbaccore import BUILTIN_ROLE_PERMS, role_allows  # noqa: E402


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. axes that must not be togglable ───────────────────────────────────────

def test_vault_critical_axes_are_not_togglable():
    for axis in ("kms", "credentials"):
        _label, togglable, reason = AXES[axis]
        assert togglable is False, f"{axis} must have no off switch"
        assert reason, f"{axis} must explain why it cannot be disabled"


def test_deployment_choice_axes_are_togglable():
    for axis in ("idp", "executors", "triggers", "brokers"):
        assert AXES[axis][1] is True, f"{axis} should be an admin decision"


def test_describe_plugins_marks_locked_axes_and_reads_the_docstring():
    class FakeProvider:
        """Wraps the vault key.

        Longer explanation nobody needs in a list.
        """
        name = "env"

    rows = describe_plugins("kms", {"env": FakeProvider()}, "inventory-service")
    assert len(rows) == 1
    row = rows[0]
    assert row["togglable"] is False
    assert row["summary"] == "Wraps the vault key."
    assert row["service"] == "inventory-service"
    assert row["axis_label"] == "Key providers"


def test_describe_plugins_is_sorted_and_empty_registry_is_empty():
    class A:
        name = "a"

    class B:
        name = "b"

    rows = describe_plugins("executors", {"b": B(), "a": A()}, "automation-service")
    assert [r["name"] for r in rows] == ["a", "b"]
    assert describe_plugins("executors", {}, "automation-service") == []


# ── 2. the toggle endpoint's own refusal + idempotence ───────────────────────

_integrations = _load("services/identity-service/app/api/integrations.py", "_integrations_api") \
    if False else None  # imported lazily below — the module pulls in app deps


def _toggle_rules():
    """Exec just the two pure functions out of the API module (importing the module
    itself would drag in FastAPI deps that CI does not install for this test)."""
    src = (ROOT / "services/identity-service/app/api/integrations.py").read_text()
    start = src.index("def toggle_refusal")
    end = src.index("class PluginToggle")
    ns = {"AXES": AXES, "status": _FakeStatus}
    exec(compile("from __future__ import annotations\n" + src[start:end], "integrations.py", "exec"), ns)  # noqa: S102
    return ns["toggle_refusal"], ns["apply_toggle"]


class _FakeStatus:
    HTTP_404_NOT_FOUND = 404
    HTTP_409_CONFLICT = 409


def test_toggle_refuses_locked_axes_and_unknown_axes():
    toggle_refusal, _ = _toggle_rules()
    assert toggle_refusal("kms")[0] == 409
    assert toggle_refusal("credentials")[0] == 409
    assert toggle_refusal("nope")[0] == 404
    assert toggle_refusal("executors") is None
    assert toggle_refusal("idp") is None


def test_apply_toggle_is_idempotent_both_ways():
    _, apply_toggle = _toggle_rules()
    d = apply_toggle({}, "executors", "salt_master", False)
    assert d["executors"] == ["salt_master"]
    # disabling twice must not duplicate
    d = apply_toggle(d, "executors", "salt_master", False)
    assert d["executors"] == ["salt_master"]
    # enabling removes it; enabling again is a no-op
    d = apply_toggle(d, "executors", "salt_master", True)
    assert d["executors"] == []
    d = apply_toggle(d, "executors", "salt_master", True)
    assert d["executors"] == []


def test_apply_toggle_does_not_mutate_its_input():
    _, apply_toggle = _toggle_rules()
    original = {"executors": ["a"]}
    apply_toggle(original, "executors", "b", False)
    assert original == {"executors": ["a"]}, "a JSON column edited in place commits nothing"


# ── 3. who may reach the integrations segment ────────────────────────────────

def test_integrations_is_superadmin_only():
    for method in ("GET", "PUT"):
        assert role_allows(BUILTIN_ROLE_PERMS["superadmin"], method, "integrations") is True
        for role in ("admin", "support", "user"):
            assert role_allows(BUILTIN_ROLE_PERMS[role], method, "integrations") is False, role


def test_integrations_is_never_grantable_to_a_token():
    assert required_scope("integrations", "GET", "integrations/state") == "admin:read"
    assert required_scope("integrations", "PUT", "integrations/plugins/idp/jumpserver") == "admin:write"
    assert "admin:read" not in AGENT_GRANTABLE and "admin:write" not in AGENT_GRANTABLE
    assert validate_agent_scopes(["admin:write"]) == ["admin:write"]


# ── 4. a failed state lookup must not re-enable a disabled executor ──────────

def test_unreachable_identity_keeps_the_last_known_disabled_set():
    state = _load("services/automation-service/app/integration_state.py", "_integration_state")
    state._reset_cache_for_tests()

    calls = {"n": 0}

    async def fake_fetch():
        calls["n"] += 1
        return {"disabled": {"executors": ["salt_master"]}, "config": {}} if calls["n"] == 1 else None

    state._fetch = fake_fetch
    state._TTL = 0  # force a refetch on every call

    async def scenario():
        assert await state.executor_disabled("salt_master") is True      # first, good read
        assert await state.executor_disabled("salt_master") is True      # identity down -> keep it
        assert await state.executor_disabled("bash_script") is False

    asyncio.run(scenario())
    assert calls["n"] >= 2, "the second call must actually have retried"
    state._reset_cache_for_tests()


def test_never_read_state_allows_running():
    """The only case where an unreachable identity-service permits a run: a fresh
    deployment that has never had anything disabled."""
    state = _load("services/automation-service/app/integration_state.py", "_integration_state2")
    state._reset_cache_for_tests()

    async def fake_fetch():
        return None

    state._fetch = fake_fetch
    assert asyncio.run(state.executor_disabled("salt_master")) is False
    state._reset_cache_for_tests()


# ── 5. the plugin endpoint must actually be importable ───────────────────────

_PLUGIN_ENDPOINT_SERVICES = (
    "identity-service", "inventory-service", "terminal-service",
    "automation-service", "zabbix-integration-service",
)


@pytest.mark.parametrize("service", _PLUGIN_ENDPOINT_SERVICES)
def test_internal_plugins_endpoint_has_its_imports(service):
    """Every name the /internal/plugins endpoint uses must be imported in that
    service's main.py.

    This is here because the first version of that endpoint was added to all five
    services with the import missing: the decorator referenced
    `require_service_token`, the module raised NameError on import, and every
    service would have failed to start. Syntax checks pass on that code — only
    resolving the names catches it.
    """
    src = (ROOT / f"services/{service}/app/main.py").read_text()
    assert "async def internal_plugins" in src, f"{service} lost its plugin endpoint"
    for name, stmt in (
        ("require_service_token", "from .deps import require_service_token"),
        ("describe_plugins", "describe_plugins"),
        ("Depends", "Depends"),
    ):
        assert name in src
        # the import must appear before the endpoint that uses it
        assert stmt in src.split("async def internal_plugins")[0].split("@app.get")[0], \
            f"{service}/main.py uses {name} without importing it"
