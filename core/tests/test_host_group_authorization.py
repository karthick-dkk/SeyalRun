"""Authorization granted through a host group must actually work — and only for
hosts in that group.

Two bugs, pointing in opposite directions, both from the same missing capability:
authorization lives in identity-service but group MEMBERSHIP lives in
inventory-service, so identity had no way to answer "is this host in that group"
and guessed twice.

  * authz/host-ids never expanded groups. Every caller gates on host_ids —
    terminal-service refuses a session whose host is not in that list — so a
    grant made through a host group authorized NOTHING. This is the reported
    behaviour.

  * authz/resolve computed `group = bool(_authz_host_groups(authz))`: "this
    authorization names some group", not "this host is in it". A grant on group A
    therefore matched a host in group B, or in no group at all. host-ids gated it
    closed for sessions, but /ssh/credentials calls resolve directly and would
    list credentials for a host the caller has no grant on.

Also: an asset must belong to at least one group, because groups are how access
is granted at scale.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDENTITY = ROOT / "services/identity-service/app/api/internal.py"
INV_HOSTS = ROOT / "services/inventory-service/app/api/hosts.py"


def _fn(src: str, name: str) -> str:
    body = src[src.index(f"async def {name}"):]
    end = body.find("\n@router")
    return body[:end] if end != -1 else body


def _code(text: str) -> str:
    """Comment lines removed. Both of these tests first matched a comment that
    QUOTES the old broken expression while explaining the fix — the same
    over-broad-substring mistake as several checks earlier in this work."""
    return "\n".join(l for l in text.split("\n") if not l.lstrip().startswith("#"))


def test_membership_endpoint_exists():
    """Guard against every assertion below passing vacuously."""
    assert '@router.get("/internal/host-group-members")' in INV_HOSTS.read_text()


# ── the gap is closed by asking, not guessing ────────────────────────────────

def test_identity_resolves_membership_from_inventory():
    src = IDENTITY.read_text()
    assert "async def _hosts_in_groups" in src
    fn = src[src.index("async def _hosts_in_groups"):]
    fn = fn[: fn.index("\ndef _authz_host_groups")]
    assert "internal/host-group-members" in fn
    assert "X-Service-Token" in fn, "the internal call must be authenticated"


def test_membership_lookup_fails_closed():
    """An inventory outage must narrow access, never widen it."""
    src = IDENTITY.read_text()
    fn = src[src.index("async def _hosts_in_groups"):]
    fn = fn[: fn.index("\ndef _authz_host_groups")]
    assert "return set()" in fn.split("except Exception")[1], (
        "the failure path must yield NO hosts, so a group grant stops applying "
        "rather than applying to everything"
    )


# ── grants through a group now authorize ─────────────────────────────────────

def test_host_ids_expands_granted_groups():
    src = IDENTITY.read_text()
    fn = _fn(src, "authz_host_ids")
    assert "await _hosts_in_groups(host_group_ids)" in fn, (
        "every caller gates on host_ids — an unexpanded group grant authorizes nothing"
    )
    # rindex, not index: the function opens with early `return {...}` guards for
    # unknown/admin users, and the expansion legitimately comes after those.
    assert fn.index("await _hosts_in_groups(host_group_ids)") < fn.rindex("return {"), \
        "expansion must happen before the response is built"


# ── resolve matches membership, not mere presence of a group ─────────────────

def test_resolve_no_longer_matches_on_having_any_group():
    src = IDENTITY.read_text()
    fn = _code(_fn(src, "authz_resolve"))
    assert "bool(_authz_host_groups(authz))" not in fn, (
        "'this authorization names a group' is not 'this host is in that group'"
    )
    assert "_covers_by_group(authz)" in fn


def test_resolve_checks_the_requested_host_against_group_members():
    src = IDENTITY.read_text()
    fn = src[src.index("def _covers_by_group"):]
    fn = fn[: fn.index("\n    def _gkey")]
    assert "host_id in _group_member_hosts" in fn


def test_membership_is_resolved_once_per_group_set():
    """This sits in the connect path; a lookup per authorization row would add a
    round trip for every grant the user holds."""
    src = IDENTITY.read_text()
    assert "_group_member_hosts: dict[str, set[str]]" in src
    assert "if key and key not in _group_member_hosts" in src


def test_direct_host_grants_still_outrank_group_grants():
    """A specific grant must keep winning over a broad one, or a group row without
    a credential shadows the direct row that has one."""
    src = IDENTITY.read_text()
    fn = src[src.index("def _rank(authz"):]
    fn = fn[: fn.index("\n    best")]
    assert "if direct and has_cred:\n            return 0" in fn
    assert "if direct:\n            return 1" in fn


# ── asset groups are mandatory ───────────────────────────────────────────────

def test_host_create_requires_a_group():
    src = INV_HOSTS.read_text()
    fn = _fn(src, "create_host")
    assert "if not payload.group_ids:" in fn
    assert "at least one asset group is required" in fn


def test_group_validation_precedes_row_creation():
    """A rejected create must leave nothing behind."""
    src = INV_HOSTS.read_text()
    fn = _fn(src, "create_host")
    assert fn.index("if not payload.group_ids:") < fn.index("host = ZAHost(")


def test_unknown_groups_are_rejected():
    """Accepting an unknown id would create a host whose membership silently does
    not exist, which is the same as having no group."""
    src = INV_HOSTS.read_text()
    fn = _fn(src, "create_host")
    assert "unknown asset group" in fn
    assert "ZAHostGroup.id.in_(payload.group_ids)" in fn
