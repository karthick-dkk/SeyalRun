"""Demo seeding must never be able to scribble on a real deployment.

Mirrors ops/seed_demo.py's guard. A demo seeder is exactly the kind of tool that
gets pointed at the wrong database once, and it writes fake payment servers and a
credential whose password is a marker string. The blast radius of getting this
wrong is someone's production inventory, so the guard is worth pinning down.
"""

from __future__ import annotations

import ipaddress
import pathlib
import re

import pytest

DEMO_PREFIX = "demo-"
SEED = pathlib.Path(__file__).resolve().parents[2] / "ops" / "seed_demo.py"


def guard_allows(existing_names: list[str], force: bool = False) -> bool:
    """Mirrors ops/seed_demo.py::_guard."""
    if force:
        return True
    return not [n for n in existing_names if not n.startswith(DEMO_PREFIX)]


def test_empty_system_is_seedable():
    assert guard_allows([]) is True


def test_system_holding_only_demo_rows_is_reseedable():
    """Re-running must be safe — the script replaces its own rows."""
    assert guard_allows(["demo-web-01", "demo-root-credential"]) is True


@pytest.mark.parametrize(
    "names",
    [
        ["prod-db-01"],
        ["demo-web-01", "prod-payments"],
        ["customer-bastion"],
    ],
)
def test_real_data_blocks_seeding(names):
    """One non-demo row is enough to refuse. This is the whole point."""
    assert guard_allows(names) is False


def test_force_overrides_but_must_be_explicit():
    assert guard_allows(["prod-db-01"], force=True) is True


def test_seed_addresses_are_all_in_reserved_test_range():
    """RFC 5737 TEST-NET-3 (203.0.113.0/24) is guaranteed non-routable, so demo
    hosts can never point at something real that someone might then connect to."""
    source = SEED.read_text()
    addresses = re.findall(r'"(\d+\.\d+\.\d+\.\d+)"', source)
    assert addresses, "no seed addresses found — did the host list move?"
    testnet3 = ipaddress.ip_network("203.0.113.0/24")
    for addr in addresses:
        assert ipaddress.ip_address(addr) in testnet3, f"{addr} is outside TEST-NET-3"


def test_seeded_credential_is_self_identifying():
    """The stored password must say what it is, so it cannot be mistaken for real
    material if someone reveals it."""
    source = SEED.read_text()
    assert "DEMO_MARKER" in source
    assert "NOT REAL" in source


def test_audit_rows_go_through_the_chain_writer():
    """Demo audit entries must be written via identity-service, not inserted
    directly. A demo that fabricates chain rows would be lying about the exact
    property it exists to demonstrate."""
    source = SEED.read_text()
    assert "/api/v1/internal/audit" in source
    assert "INSERT INTO za_audit_logs" not in source.upper()
