"""Every alembic migration must link to a revision that exists.

I added a migration whose down_revision was taken from the FILENAME prefix
("010") rather than from the `revision =` line inside that file, which in
automation-service is "auto_010". Alembic then failed with KeyError: '010' on
every deploy, retrying ten times — the schedule-timezone column was never
created, and every future automation migration was blocked behind it.

Two things let it through:

  * the test covering that feature asserted the migration FILE contained the
    right column and default. It never ran alembic, so a chain that could not be
    walked looked identical to one that could. Same shape as every other "assert
    the code exists" failure in this work.
  * the deploy retries migrations and the service still reports healthy, so the
    containers came up green with the migration never applied. The failure was
    only visible in the deploy log.

This walks each service's chain the way alembic does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"


def _revisions(service: Path) -> dict[str, str | None]:
    """revision id -> down_revision, for one service."""
    out: dict[str, str | None] = {}
    for f in (service / "migrations/versions").glob("*.py"):
        if f.name == "__init__.py":
            continue
        src = f.read_text()
        rev = re.search(r'^revision\s*=\s*["\'](\w+)["\']', src, re.M)
        down = re.search(r'^down_revision\s*=\s*(?:["\'](\w+)["\']|None)', src, re.M)
        assert rev, f"{f.name} declares no revision"
        out[rev.group(1)] = down.group(1) if (down and down.group(1)) else None
    return out


def _services() -> list[Path]:
    return sorted(d for d in SERVICES.iterdir() if (d / "migrations/versions").is_dir())


def test_services_have_migrations():
    """Guard against the parametrisation below being empty."""
    svcs = _services()
    assert svcs, "no services with migrations found"
    assert _revisions(SERVICES / "automation-service"), "automation revisions did not parse"


@pytest.mark.parametrize("service", _services(), ids=lambda p: p.name)
def test_every_down_revision_exists(service: Path):
    """The exact failure: down_revision "010" against a chain whose ids are
    "auto_010". Taking it from the filename rather than the file is the trap."""
    revs = _revisions(service)
    dangling = {
        rev: down for rev, down in revs.items()
        if down is not None and down not in revs
    }
    assert not dangling, (
        f"{service.name}: down_revision points at a revision that does not exist "
        f"{dangling} — known revisions are {sorted(revs)}"
    )


@pytest.mark.parametrize("service", _services(), ids=lambda p: p.name)
def test_exactly_one_base_and_one_head(service: Path):
    """Two heads means alembic cannot decide what to upgrade to; two bases means
    a branch nobody intended."""
    revs = _revisions(service)
    bases = [r for r, d in revs.items() if d is None]
    downs = {d for d in revs.values() if d}
    heads = [r for r in revs if r not in downs]
    assert len(bases) == 1, f"{service.name}: expected one base, found {bases}"
    assert len(heads) == 1, f"{service.name}: expected one head, found {heads}"


@pytest.mark.parametrize("service", _services(), ids=lambda p: p.name)
def test_the_chain_reaches_the_base(service: Path):
    """Walk it the way alembic does, so a cycle or an orphan island is caught."""
    revs = _revisions(service)
    downs = {d for d in revs.values() if d}
    head = next(r for r in revs if r not in downs)
    seen: set[str] = set()
    cur: str | None = head
    while cur is not None:
        assert cur not in seen, f"{service.name}: cycle at {cur}"
        seen.add(cur)
        cur = revs[cur]
    assert seen == set(revs), (
        f"{service.name}: {sorted(set(revs) - seen)} are unreachable from the head"
    )
