"""SessionBroker invariants: delegating a live session to an external PAM must
not become a way around SeyalRun's own controls, and a deployment that runs no
external PAM must end up with no broker at all."""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest

from libs.pluginbase import discover_plugins
from libs.pluginbase.interfaces import BrokeredSession, SessionBroker, SessionTarget

TARGET = SessionTarget(host_id="h1", address="10.0.0.1", ssh_username="root")


class _Broker(SessionBroker):
    """Minimal conforming broker: only the three required methods."""

    name = "fake"

    def __init__(self) -> None:
        self.opened = 0
        self.closed: tuple[str, str] | None = None

    async def authorize(self, user: dict, target: SessionTarget) -> bool:
        return bool(user.get("permitted"))

    async def open_session(self, user: dict, target: SessionTarget) -> BrokeredSession:
        self.opened += 1
        return BrokeredSession(ok=True, broker_session_id="ext-1")

    async def close_session(self, broker_session_id: str, reason: str) -> None:
        self.closed = (broker_session_id, reason)


async def _connect(broker: _Broker, user: dict) -> BrokeredSession:
    """The dispatch order terminal-service is required to follow."""
    if not await broker.authorize(user, TARGET):
        return BrokeredSession(ok=False, reason="denied by external PAM")
    return await broker.open_session(user, TARGET)


def test_denied_authorize_never_opens_a_session():
    """A denial must stop the connect path before any transport is established.

    Regression guard for the case where the broker would happily connect: the
    gate is what makes delegation safe, so it must be checked first, not merely
    reported alongside a session that already exists.
    """
    broker = _Broker()
    result = asyncio.run(_connect(broker, {"permitted": False}))

    assert result.ok is False
    assert result.broker_session_id is None
    assert broker.opened == 0


def test_permitted_user_gets_a_correlatable_session():
    broker = _Broker()
    result = asyncio.run(_connect(broker, {"permitted": True}))

    assert result.ok is True
    # Required for audit reconciliation against the external PAM's own log.
    assert result.broker_session_id == "ext-1"
    assert broker.opened == 1


def test_close_session_records_its_reason():
    broker = _Broker()
    asyncio.run(broker.close_session("ext-1", "idle timeout"))
    assert broker.closed == ("ext-1", "idle timeout")


def test_optional_hooks_have_safe_defaults():
    """A broker with no asset catalogue and no external UI stays conforming."""
    broker = _Broker()

    assert asyncio.run(broker.sync_targets([TARGET])) == {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
    }
    with pytest.raises(NotImplementedError):
        asyncio.run(broker.mint_launch_token({}, TARGET))


def test_incomplete_broker_cannot_be_instantiated():
    class Incomplete(SessionBroker):
        name = "incomplete"

        async def authorize(self, user: dict, target: SessionTarget) -> bool:
            return True

    with pytest.raises(TypeError):
        Incomplete()  # missing open_session/close_session


def test_discovery_registers_the_broker_axis(tmp_path):
    """The new axis must need no change to discovery.py."""
    pkg = tmp_path / "app" / "plugins" / "brokers"
    pkg.mkdir(parents=True)
    for parent in (tmp_path / "app", tmp_path / "app" / "plugins", pkg):
        (parent / "__init__.py").write_text("")
    (pkg / "demo.py").write_text(
        textwrap.dedent(
            """
            from libs.pluginbase.interfaces import BrokeredSession, SessionBroker

            class DemoBroker(SessionBroker):
                name = "demo"
                async def authorize(self, user, target): return True
                async def open_session(self, user, target): return BrokeredSession(ok=True)
                async def close_session(self, broker_session_id, reason): pass
            """
        )
    )

    sys.path.insert(0, str(tmp_path))
    try:
        registry = discover_plugins("app.plugins.brokers", SessionBroker)
    finally:
        sys.path.remove(str(tmp_path))
        for mod in [m for m in sys.modules if m.startswith("app.")]:
            del sys.modules[mod]

    assert list(registry) == ["demo"]
    assert isinstance(registry["demo"], SessionBroker)


def test_absent_axis_yields_no_brokers():
    """Standalone deployments ship no broker module and must run native-only."""
    assert discover_plugins("app.plugins.definitely_not_present", SessionBroker) == {}
