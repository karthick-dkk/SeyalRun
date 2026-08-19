"""Every routed segment must be reachable by someone, and every gated segment
must exist in the permission matrix.

This closes a bug class that has now shipped three separate times, each with the
same shape — a capability the API implements, gated on a segment nobody was ever
granted, so the feature is dead for every role that should have it:

  * `api-tokens`  — self-service PAT endpoints, every handler scoped by
                    current_user_id, sitting in the superadmin-only _SYSTEM group.
  * `assets`      — escaped only because nav_permissions() special-cases it.
  * `notifications` — routed to a real upstream, present in no role's perms at
                    all, so the WebSocket upgrade closed 4403 for admin, support
                    and user; only superadmin passed, on all=True.

rbaccore's own comment asserts "every real segment from proxy.SERVICE_ROUTES
appears in exactly one group below". That sentence was false when it was
written, and stayed false through two more instances. This makes it executable.

A source-shape check by necessity: the defect lives in the gap between one
service's route table and another library's matrix, so neither side's unit tests
can see it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROXY = ROOT / "services/api-gateway/app/proxy.py"
WS_PROXY = ROOT / "services/api-gateway/app/ws_proxy.py"
RBAC_CORE = ROOT / "libs/rbaccore/__init__.py"
GATEWAY_RBAC = ROOT / "services/api-gateway/app/rbac.py"

# Segments deliberately outside the role matrix, each with a stated reason.
# Anything not listed here MUST appear in the matrix — that is the point.
EXEMPT = {
    # Always-allowed: login/logout/refresh must work before any role exists.
    "auth",
    # Internal service-to-service only; never proxied on behalf of a browser role.
    "test-connection",
}


def _dict_keys(src: str, name: str) -> set[str]:
    """Literal keys of a module-level dict assignment, parsed rather than regexed."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            value = node.value
        elif isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            value = node.value
        else:
            continue
        assert isinstance(value, ast.Dict), f"{name} is not a dict literal"
        return {k.value for k in value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"{name} not found in source")


def _dict_values(src: str, name: str) -> set[str]:
    """Literal string values of a module-level dict assignment."""
    tree = ast.parse(src)
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
        )
        if getattr(target, "id", None) == name and isinstance(node.value, ast.Dict):
            return {v.value for v in node.value.values if isinstance(v, ast.Constant)}
    raise AssertionError(f"{name} not found in source")


# Keys of a role's dict that describe the role itself, not a routed segment.
_ROLE_META = {"all", "flags", "perms"}


def _matrix_segments() -> set[str]:
    """Every segment any built-in role can be granted: the group lists plus every
    key of each role's `perms`. Parsed with ast, not regex — an earlier regex
    version scraped `"flags": [...]` as though it were a segment."""
    tree = ast.parse(RBAC_CORE.read_text())
    segs: set[str] = set()
    for node in tree.body:
        # group lists: _INVENTORY = [...], _SELF = [...], ...
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", "")
            if name.startswith("_") and name.isupper() and isinstance(node.value, ast.List):
                segs |= {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
        # BUILTIN_ROLE_PERMS: {role: {"perms": {segment: [...]}}}
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
        )
        if getattr(target, "id", None) == "BUILTIN_ROLE_PERMS" and isinstance(node.value, ast.Dict):
            for role_def in node.value.values:
                if not isinstance(role_def, ast.Dict):
                    continue
                for k, v in zip(role_def.keys, role_def.values):
                    if isinstance(k, ast.Constant) and k.value == "perms" and isinstance(v, ast.Dict):
                        segs |= {
                            kk.value for kk in v.keys
                            if isinstance(kk, ast.Constant) and kk.value not in _ROLE_META
                        }
    return segs - _ROLE_META


def test_parsers_find_something():
    """Guard against every assertion below passing vacuously."""
    assert len(_dict_keys(PROXY.read_text(), "SERVICE_ROUTES")) > 20
    assert len(_matrix_segments()) > 15


def test_every_routed_segment_is_in_the_permission_matrix():
    routed = _dict_keys(PROXY.read_text(), "SERVICE_ROUTES")
    matrix = _matrix_segments()
    missing = sorted(routed - matrix - EXEMPT)
    assert not missing, (
        "these segments are routed to a real upstream but appear in no role's "
        f"permissions, so is_authorized() refuses them for every role except "
        f"superadmin (all=True): {missing}. Either grant them in libs/rbaccore or "
        "add them to EXEMPT here with the reason."
    )


def test_every_ws_gated_segment_is_in_the_permission_matrix():
    """The WS upgrade runs the same matrix check and closes 4403 on failure —
    where `notifications` died."""
    # ast, not regex: ws_proxy.py has other string-to-string dicts (log `extra=`
    # payloads), and a loose pattern scraped "from_client" out of one of them.
    mapped = set(_dict_values(WS_PROXY.read_text(), "_WS_RBAC_SEGMENT"))
    assert mapped, "could not parse _WS_RBAC_SEGMENT — this test would pass vacuously"
    missing = sorted(mapped - _matrix_segments())
    assert not missing, f"WS segments absent from the RBAC matrix (upgrade closes 4403): {missing}"


def test_every_ws_route_has_an_rbac_segment_mapping():
    """A WS route with no entry in _WS_RBAC_SEGMENT falls back to the raw path
    segment; if that is not a matrix segment the upgrade is refused for everyone
    but superadmin — silently, and only at connect time."""
    src = WS_PROXY.read_text()
    routed = _dict_keys(src, "WS_SERVICE_ROUTES")
    missing = sorted(routed - _dict_keys(src, "_WS_RBAC_SEGMENT"))
    assert not missing, f"WS routes with no explicit RBAC segment mapping: {missing}"


def test_nav_permission_segments_are_real_or_declared_pseudo():
    """nav_permissions() drives which pages the SPA shows. A nav entry naming a
    segment nobody can hold renders a permanently hidden page; one naming a
    segment that is not routed renders a page whose API 404s."""
    # _NAV_SEGMENTS maps a nav AREA to (method, segment). The area name is a page
    # id ("dashboard", "terminal"); the segment is the second tuple element. An
    # earlier version asserted against the keys and flagged every page name.
    tree = ast.parse(GATEWAY_RBAC.read_text())
    nav: set[str] = set()
    for node in ast.walk(tree):
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if getattr(target, "id", None) == "_NAV_SEGMENTS" and isinstance(node.value, ast.Dict):
            for v in node.value.values:
                if isinstance(v, ast.Tuple) and len(v.elts) == 2 and isinstance(v.elts[1], ast.Constant):
                    nav.add(v.elts[1].value)
    assert nav, "could not parse the nav segment map"
    known = _matrix_segments() | _dict_keys(PROXY.read_text(), "SERVICE_ROUTES")
    # Two documented non-segments:
    #   'assets'          — page-level pseudo-segment resolved against 'hosts'; the
    #                       special case that let this whole bug class hide so long.
    #   'auth/mfa/setup'  — a full path, not a segment: is_authorized() routes it to
    #                       can_use_mfa() before segment resolution (rbac.py:75).
    unknown = sorted(nav - known - {"assets", "auth/mfa/setup"})
    assert not unknown, f"nav segments that are neither routed nor grantable: {unknown}"


@pytest.mark.parametrize("segment", sorted(_matrix_segments()))
def test_no_granted_segment_is_a_dead_grant(segment: str):
    """The converse direction: a segment granted in the matrix but routed
    nowhere is a permission that can be assigned and reviewed but does nothing —
    it makes an access review report coverage the system does not have."""
    routed = _dict_keys(PROXY.read_text(), "SERVICE_ROUTES")
    ws = set(_dict_values(WS_PROXY.read_text(), "_WS_RBAC_SEGMENT"))
    # Pseudo-segments: enforced by handler-level checks, not by path routing.
    pseudo = {"playbook-run", "metrics", "assets"}
    assert segment in routed | ws | pseudo, (
        f"'{segment}' is grantable but routed nowhere — either it is dead, or it "
        "is a pseudo-segment and belongs in the documented set above"
    )
