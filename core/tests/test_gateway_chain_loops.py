"""A ProxyJump chain must never route through the same machine twice.

Zone CYCLES were already handled: _validate_parent_zone rejects one at write time
on both create and update, and the ancestry walk carries its own `seen` guard. So
the tree cannot loop.

What was unguarded is the same HOST appearing twice in the RESOLVED chain, which
a perfectly acyclic tree can still produce:

  * two distinct zones may legitimately both name the same gateway machine.
    Nesting them yields "ssh -J gw,gw" — a hop from a host to itself.
  * a zone's gateway may BE the host being connected to, so the chain jumps
    through the machine it is already trying to reach.

Both stall the connection rather than failing it, which presents as a hang with
no error — the worst shape for an operator to debug. Neither is visible from one
side alone: inventory cannot know which host a chain is being built for, and
terminal-service cannot see the zone tree.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZONES = ROOT / "services/inventory-service/app/api/zones.py"
TERMINAL = ROOT / "services/terminal-service/app/ws/terminal.py"


def _chain_fn() -> str:
    src = ZONES.read_text()
    body = src[src.index("async def zone_gateway_chain"):]
    return body[: body.index("\n@router")]


def test_chain_endpoint_parses():
    """Guard against every assertion below passing vacuously."""
    assert "hops" in _chain_fn()


# ── the tree itself cannot loop (pre-existing, pinned so it stays true) ───────

def test_zone_cycles_are_rejected_at_write_time():
    src = ZONES.read_text()
    fn = src[src.index("async def _validate_parent_zone"):]
    fn = fn[: fn.index("\n@router")]
    assert "zone cannot be its own parent" in fn
    assert "would create a zone cycle" in fn
    assert "seen" in fn, "the ancestry walk needs its own guard against pre-existing cycles"


def test_cycle_check_runs_on_both_create_and_update():
    """Checking only on create leaves the update path able to build the cycle."""
    src = ZONES.read_text()
    assert src.count("await _validate_parent_zone(") >= 2


def test_chain_walk_terminates_even_on_a_corrupt_tree():
    """Data written before the validator existed could still contain a cycle."""
    fn = _chain_fn()
    assert "current_id not in seen" in fn


# ── the resolved chain cannot repeat a host ──────────────────────────────────

def test_duplicate_gateway_endpoints_are_dropped():
    fn = _chain_fn()
    assert "seen_endpoints" in fn, "a repeated gateway host must not become a second hop"
    # Anchored on the PROPERTY, not the variable name: the first version pinned
    # `gw.host`, which broke the moment gateways became hosts and the local was
    # renamed — while the normalisation it was checking was still correct.
    assert re.search(r'\.strip\(\)\.lower\(\),\s*int\(\w+\[?"?port"?\]? or 22\)', fn), (
        "dedupe must compare host AND port, normalised — 'GW' and 'gw ' are the same machine"
    )


def test_duplicates_are_reported_not_silently_dropped():
    """A duplicated gateway usually means the zone tree is misconfigured, and a
    chain that quietly works while the topology is wrong is how it stays wrong."""
    fn = _chain_fn()
    assert "skipped_duplicate_gateways" in fn


def test_outermost_occurrence_is_the_one_kept():
    """The chain is built root-first; the outermost hop is the one reachable from
    where the connection starts."""
    fn = _chain_fn()
    add_at = fn.index("seen_endpoints.add(endpoint)")
    skip_at = fn.index("if endpoint in seen_endpoints:")
    assert skip_at < add_at, "the check must precede the add, or the first hop drops itself"


def test_a_gateway_that_is_the_target_host_is_skipped():
    """Inventory cannot catch this one — it does not know which host the chain is
    for. terminal-service does."""
    src = TERMINAL.read_text()
    assert "_target = " in src
    block = src[src.index("_target = "):][:900]
    assert "continue" in block, "the hop must be dropped"
    assert "logger.warning" in block, (
        "silently dropping it hides a misconfigured zone; it stalls rather than "
        "errors, so the log is the only signal"
    )


def test_target_comparison_is_normalised():
    src = TERMINAL.read_text()
    block = src[src.index("_target = "):][:600]
    assert ".strip().lower()" in block
    assert "int(hop.get(\"port\") or 22)" in block, "a default port must compare equal to an explicit 22"
