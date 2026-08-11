"""No administrative section may be reachable at two URLs.

AppShell, AdminView and SettingsView each used to carry a hand-written copy of
the admin nav tree, and the three had drifted: AppShell's had no Platform group
at all while AdminView's had five entries in it. The router's
VALID_ADMIN_SECTIONS still listed every Settings section, so seven components
(integration, platform, health, security, housekeeping, log-backend, audit)
were mounted at both /admin/<s> and /settings/<s> — both requiresAdmin, both
gating the same RBAC area, so duplicate destinations rather than two permission
levels.

src/config/adminSections.ts is now the only list. This test pins that: exactly
one definition of the sections, every consumer deriving from it, and no slug
owned by both shells.

A source-shape check on purpose — the invariant is "there is one list", which is
structural. A rendering test would prove a page works without proving the
duplication cannot come back.

Regression: ISSUE-007 — found by /qa on 2026-08-09
Report: docs/ui-audit.md
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "services/frontend/src"
REGISTRY = SRC / "config/adminSections.ts"
CONSUMERS = {
    "AppShell": SRC / "components/layout/AppShell.vue",
    "AdminView": SRC / "views/AdminView.vue",
    "SettingsView": SRC / "views/SettingsView.vue",
    "router": SRC / "router/index.ts",
}


def _sections() -> list[tuple[str, str]]:
    """(slug, shell) for every entry in the registry."""
    body = REGISTRY.read_text()
    body = body[body.index("export const SECTIONS"):body.index("export function pathOf")]
    return [
        (m.group(1), m.group(2))
        for m in re.finditer(r"slug:\s*'([^']+)'.*?shell:\s*'(admin|settings)'", body, re.S)
    ]


def test_registry_parses():
    """Guard against the regex silently matching nothing, which would make every
    check below vacuously pass."""
    slugs = [s for s, _ in _sections()]
    assert {"users", "audit", "credentials"} <= set(slugs), f"parsed unexpectedly: {slugs}"


def test_no_section_is_owned_by_two_shells():
    """The duplication itself: one slug, one owning shell, one canonical URL."""
    seen: dict[str, str] = {}
    dupes = []
    for slug, shell in _sections():
        if slug in seen and seen[slug] != shell:
            dupes.append(slug)
        seen[slug] = shell
    assert not dupes, f"sections claimed by both shells: {dupes}"


def test_registry_is_the_only_section_list():
    """No consumer may reintroduce a local copy. They must import instead."""
    offenders = []
    for name, path in CONSUMERS.items():
        src = path.read_text()
        if "@/config/adminSections" not in src:
            offenders.append(f"{name} does not import the registry")
        # A literal list of section objects is what the copies looked like.
        if re.search(r"\{\s*area:\s*'admin\.\w[^}]*to:\s*'/(admin|settings)/", src):
            offenders.append(f"{name} declares section objects inline again")
    assert not offenders, "; ".join(offenders)


@pytest.mark.parametrize("slug", [s for s, sh in _sections() if sh == "settings"])
def test_moved_sections_are_not_rendered_by_adminview(slug: str):
    """A Settings section rendered by AdminView would be reachable at /admin/<s>
    again even with the redirect in place, because beforeEnter only guards the
    routes it knows about."""
    src = CONSUMERS["AdminView"].read_text()
    assert f"section === '{slug}'" not in src, (
        f"AdminView still renders '{slug}', which belongs to the Settings shell"
    )


def test_router_redirects_moved_sections():
    src = CONSUMERS["router"].read_text()
    assert "MOVED_TO_SETTINGS" in src, "router must redirect the moved /admin URLs"
    assert "VALID_ADMIN_SECTIONS = SECTIONS.filter" in src, (
        "route validation must derive from the registry, not a second hand-written list"
    )


def test_zones_is_admin_only_and_has_one_entry_point():
    """Zone/gateway topology is admin-or-above (libs/rbaccore grants the `zones`
    segment to admin and superadmin and to nobody else). It used to be reachable
    both at /zones, from a top-level nav item, and at /admin/zones behind
    requiresAdmin — one page, two doors, only one of them gated. /zones now
    redirects, so the admin gate cannot be walked around."""
    owner = {slug: shell for slug, shell in _sections()}
    assert owner.get("zones") == "admin", "zones must be owned by the admin shell"

    router = CONSUMERS["router"].read_text()
    assert "redirect: '/admin/zones'" in router, "/zones must redirect to the gated route"

    shell = CONSUMERS["AppShell"].read_text()
    assert 'to="/zones"' not in shell, (
        "the top-level Zones nav item is a second door to an admin page"
    )
