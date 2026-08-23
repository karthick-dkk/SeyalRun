"""A gateway is a host marked host_type="gateway", and a zone may hold several.

A gateway used to be a za_gateways row with its own shape — an address, a
username, a credential id, nothing else. It could not appear in Assets, could not
belong to an asset group, could not be authorized, and could not be given a
credential through the normal credential paths. Every one of those either needed
a special case or simply did not exist for gateways.

It is an ordinary host that happens to be used as a jump point, so it is modelled
as one. Everything hosts already have then applies to it for free.

A zone may hold SEVERAL gateways — a redundant pair, or a chain through a DMZ —
so each contributes a hop, in an explicit order rather than by creation time:
which gateway comes first is a topology decision, not an accident of typing
order.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INV = ROOT / "services/inventory-service"
MODELS = INV / "app/models.py"
ZONES = INV / "app/api/zones.py"
HOSTS = INV / "app/api/hosts.py"
SCHEMAS = INV / "app/schemas.py"
MIGRATIONS = INV / "migrations/versions"


def _chain_fn() -> str:
    src = ZONES.read_text()
    body = src[src.index("async def zone_gateway_chain"):]
    return body[: body.index("\n@router")]


def test_host_model_carries_the_type():
    """Guard against every assertion below passing vacuously."""
    src = MODELS.read_text()
    block = src[src.index("class ZAHost("): src.index("class ", src.index("class ZAHost(") + 10)]
    assert 'host_type: Mapped[str]' in block
    assert 'default="server"' in block, "existing hosts must stay servers"
    assert "gateway_order" in block


# ── a gateway is a host ──────────────────────────────────────────────────────

def test_chain_prefers_gateway_hosts():
    fn = _chain_fn()
    assert 'ZAHost.host_type == "gateway"' in fn
    assert "ZAHost.zone_id == z.id" in fn


def test_disabled_gateways_are_not_used():
    """A gateway taken out of service must stop being a hop, not keep being one."""
    assert "ZAHost.enabled.is_(True)" in _chain_fn()


def test_gateway_host_logs_in_with_its_own_credential():
    """The whole benefit of modelling it as a host. Without resolving the link the
    hop carries no login and the jump fails at connect time with nothing
    explaining why."""
    fn = _chain_fn()
    assert "ZACredentialHostLink.credential_id" in fn
    assert "ZACredentialHostLink.host_id == h.id" in fn


def test_host_type_round_trips_through_the_api():
    """A field the model stores but the API drops is a field the UI cannot set."""
    schemas = SCHEMAS.read_text()
    assert re.search(r"class HostCreate.*?host_type: str", schemas, re.S)
    assert re.search(r"class HostOut.*?host_type: str", schemas, re.S)
    hosts = HOSTS.read_text()
    assert "host_type=payload.host_type" in hosts, "create must persist it"
    assert "host_type=host.host_type" in hosts, "reads must return it"


# ── several gateways per zone ────────────────────────────────────────────────

def test_every_gateway_in_a_zone_contributes_a_hop():
    """The old code took .limit(1) — a zone's second gateway was silently ignored."""
    fn = _chain_fn()
    assert "for cand in candidates:" in fn
    assert ".limit(1)" not in fn.split("gw_hosts = ")[1].split(")).scalars()")[0], (
        "gateway selection must not be limited to one per zone"
    )


def test_gateway_order_is_explicit_not_creation_order():
    fn = _chain_fn()
    assert "order_by(ZAHost.gateway_order" in fn


def test_duplicate_endpoints_are_still_deduped_across_multiple_gateways():
    """More gateways per zone means more chances to repeat a machine."""
    fn = _chain_fn()
    assert "seen_endpoints" in fn
    assert fn.index("if endpoint in seen_endpoints:") < fn.index("seen_endpoints.add(endpoint)")


# ── migration safety ─────────────────────────────────────────────────────────

def test_migration_copies_rather_than_moves():
    """A deployment whose gateways have not been reviewed must keep connecting."""
    mig = sorted(MIGRATIONS.glob("*_hosts_as_gateways.py"))
    assert mig, "no migration introduces gateway hosts"
    src = mig[-1].read_text()
    assert "DROP TABLE" not in src.upper(), "the old table must survive the migration"
    assert "INSERT INTO za_hosts" in src


def test_migration_is_idempotent():
    """Re-running must not duplicate every gateway."""
    src = sorted(MIGRATIONS.glob("*_hosts_as_gateways.py"))[-1].read_text()
    assert "existing" in src and "continue" in src


def test_migration_carries_the_login_across():
    """A copied gateway with no credential cannot be jumped through."""
    src = sorted(MIGRATIONS.glob("*_hosts_as_gateways.py"))[-1].read_text()
    assert "za_credential_host_links" in src, (
        "the credential link table is za_credential_host_links — an INSERT into a "
        "mis-named table fails silently in the sense that the gateway is copied "
        "without a login"
    )


def test_chain_falls_back_to_the_legacy_table():
    """Until a deployment's gateways are reviewed, the old rows must still work."""
    fn = _chain_fn()
    assert "if not candidates:" in fn
    assert "ZAGateway" in fn
