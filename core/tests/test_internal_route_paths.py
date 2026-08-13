"""Cross-service calls must use the path the callee actually mounts.

Credential rotation returned 502 for every attempt because inventory-service
posted to `/internal/job-runs` while automation-service mounts that router with
`prefix="/api/v1"`, making the real path `/api/v1/internal/job-runs`. The call
404'd, and rotation — the control R-6 is about being able to perform — had
never worked.

It survived because it looks right in isolation. The two other calls in the
same file get it right, so a reader comparing them has to notice one missing
prefix among three otherwise-identical lines.

A source-shape check: the mismatch is between a string in one service and a
router mount in another, which no unit test in either service would catch, and
an integration test would need both running.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"


def _mount_prefixes(service: str) -> dict[str, str]:
    """router module name -> prefix it is mounted under in main.py"""
    main = (SERVICES / service / "app/main.py").read_text()
    out = {}
    for m in re.finditer(
        r"from \.api\.(\w+) import router as (\w+)", main
    ):
        module, alias = m.group(1), m.group(2)
        inc = re.search(rf"include_router\({alias}\s*,\s*prefix=\"([^\"]*)\"", main)
        out[module] = inc.group(1) if inc else ""
    return out


def test_mounts_are_discoverable():
    """Guard against the parsing above returning nothing and passing vacuously."""
    prefixes = _mount_prefixes("automation-service")
    assert "internal" in prefixes, f"could not read automation-service mounts: {prefixes}"


@pytest.mark.parametrize("caller", ["inventory-service", "identity-service", "terminal-service"])
def test_calls_to_automation_internal_use_the_mounted_prefix(caller: str):
    prefix = _mount_prefixes("automation-service").get("internal", "")
    caller_dir = SERVICES / caller / "app"
    if not caller_dir.exists():
        pytest.skip(f"{caller} has no app/")
    bad = []
    for f in caller_dir.rglob("*.py"):
        lines = f.read_text().split("\n")
        for n, line in enumerate(lines, 1):
            # Only a literal path handed straight to an httpx client, where nothing
            # can add the prefix later. Helper-based calls are excluded on purpose:
            # terminal-service's _identity_get builds f"{url}/api/v1{path}", so the
            # bare "/internal/..." it passes is correct. Flagging those was a false
            # positive on the first version of this test.
            if re.search(r'client\.(get|post|put|delete)\(\s*$', line) or \
               re.search(r'client\.(get|post|put|delete)\(\s*f?"', line):
                window = "\n".join(lines[n - 1:n + 3])
                m2 = re.search(r'"\s*(/internal/[^"]*)"', window)
                if m2 and prefix and not m2.group(1).startswith(prefix):
                    bad.append(f"{f.relative_to(SERVICES)}:{n}: {m2.group(1)}")
    assert not bad, (
        f"automation-service mounts its internal router at {prefix!r}; these calls omit it "
        f"and will 404:\n  " + "\n  ".join(bad)
    )
