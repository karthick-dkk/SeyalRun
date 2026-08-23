"""Asset type is a constrained value, set from the form, and survives an edit.

Three defects in what I shipped for gateways-as-hosts, found by exercising the
deployed API rather than re-reading the code:

  * host_type was a bare `str`. {"host_type": "totally-made-up"} was accepted and
    stored — the host is then neither a server nor a gateway, so it drops out of
    gateway resolution while still looking like a configured jump point. The UI
    cannot be the gate; the API is reachable directly.

  * the Assets form did not expose it, so a gateway could only be created by
    hand-crafting a request.

  * update_host never assigned it, so editing ANY field of a gateway silently
    demoted it to a server: it would disappear from its zone's chain and
    connections would start failing for a reason nothing in the edit suggested.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "services/inventory-service/app/schemas.py"
HOSTS = ROOT / "services/inventory-service/app/api/hosts.py"
ASSETS = ROOT / "services/frontend/src/views/AssetsView.vue"


def test_schema_parses():
    """Guard against every assertion below passing vacuously."""
    assert "class HostCreate" in SCHEMAS.read_text()


# ── constrained, server-side ─────────────────────────────────────────────────

def test_host_type_is_a_closed_set():
    src = SCHEMAS.read_text()
    m = re.search(r'HostType = Literal\[([^\]]*)\]', src)
    assert m, "host_type must be a Literal, not a free string"
    assert set(re.findall(r'"(\w+)"', m.group(1))) == {"server", "gateway"}
    assert re.search(r"host_type:\s*HostType", src), "HostCreate must use it"


def test_os_type_is_also_constrained():
    src = SCHEMAS.read_text()
    assert re.search(r'OsType = Literal\[', src)
    assert re.search(r"os_type:\s*OsType", src)


def test_gateway_order_is_bounded():
    """It only orders hops; a negative or absurd value is a typo that would
    silently reshuffle a connection chain."""
    src = SCHEMAS.read_text()
    m = re.search(r"gateway_order:\s*int\s*=\s*Field\(([^)]*)\)", src)
    assert m and "ge=0" in m.group(1) and "le=" in m.group(1)


def test_port_is_a_valid_port():
    src = SCHEMAS.read_text()
    m = re.search(r"port:\s*int\s*=\s*Field\(([^)]*)\)", src)
    assert m and "ge=1" in m.group(1) and "le=65535" in m.group(1)


# ── the edit path keeps it ───────────────────────────────────────────────────

def test_update_persists_the_type():
    src = HOSTS.read_text()
    fn = src[src.index("async def update_host"):]
    fn = fn[: fn.index("\n@router")]
    assert "host.host_type = payload.host_type" in fn, (
        "editing any field would otherwise demote a gateway back to a server"
    )
    assert "host.gateway_order = payload.gateway_order" in fn


@pytest.mark.parametrize("field", ["host_type", "gateway_order"])
def test_create_persists_the_type(field: str):
    src = HOSTS.read_text()
    fn = src[src.index("async def create_host"):]
    fn = fn[: fn.index("\n@router")]
    assert f"{field}=payload.{field}" in fn


# ── the form can set it ──────────────────────────────────────────────────────

def test_form_offers_both_asset_types():
    src = ASSETS.read_text()
    assert 'v-model="assetForm.host_type"' in src
    assert 'value="gateway"' in src and 'value="server"' in src


def test_form_sends_the_type():
    """A selector the payload drops is a selector that does nothing."""
    src = ASSETS.read_text()
    assert "host_type: assetForm.host_type" in src
    assert "gateway_order:" in src


def test_gateway_order_is_only_offered_for_gateways():
    src = ASSETS.read_text()
    assert "v-if=\"assetForm.host_type === 'gateway'\"" in src


def test_switching_back_to_server_clears_the_order():
    """A stale ordering left on a demoted gateway would resurface if it were ever
    made a gateway again."""
    src = ASSETS.read_text()
    assert "assetForm.host_type === 'gateway' ? (assetForm.gateway_order || 0) : 0" in src


def test_list_distinguishes_a_gateway():
    src = ASSETS.read_text()
    assert "h.host_type === 'gateway'" in src
