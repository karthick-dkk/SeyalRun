"""How authz_resolve combines actions across authorization rows.

A user's allowed actions on a host used to come from the SINGLE best-matching
za_authorization row. Splitting a login's grants over two direct rows (one
`ssh,sftp`, one `upload`) silently dropped whichever the DB didn't happen to
return first — same rank, no ORDER BY, arbitrary winner.

The fix unions actions across every row AT THE SAME BEST RANK. The security
property is that this is same-rank ONLY: a broader group grant (rank 2/3) is
still fully shadowed by any direct rule (rank 0/1), so a narrow direct rule can
still restrict a user below their group's access. Union-everything would have
removed that restriction — union-within-best-rank does not.

These tests EXECUTE the real helper (extracted from the service source, not
re-typed) against both the footgun and the restriction case.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTERNAL = ROOT / "services/identity-service/app/api/internal.py"


def _load_helper():
    """Exec just the pure _union_best_rank_actions function from the real source,
    with no service imports, and return the callable."""
    src = INTERNAL.read_text()
    start = src.index("def _union_best_rank_actions")
    # slice to the next top-level def/class (a line starting in column 0)
    rest = src[start:]
    m = re.search(r"\n(?=def |class )", rest[3:])
    body = rest[: m.start() + 3] if m else rest
    ns: dict = {}
    exec(compile(body, str(INTERNAL), "exec"), ns)  # noqa: S102 - a single pure function from our own source
    return ns["_union_best_rank_actions"]


UNION = _load_helper()

# rank constants matching internal.py _rank(): 0 direct+cred, 1 direct,
# 2 group+cred, 3 group.
DIRECT, GROUP = 0, 2


def test_empty_and_single():
    assert UNION([]) == []
    assert UNION([(DIRECT, ["ssh"])]) == ["ssh"]


def test_split_grant_over_two_direct_rows_is_unioned():
    """The footgun: two direct rows, different actions — both apply now."""
    got = UNION([(DIRECT, ["ssh", "sftp"]), (DIRECT, ["upload", "download"])])
    assert set(got) == {"ssh", "sftp", "upload", "download"}


def test_union_is_order_preserving_and_deduped():
    got = UNION([(DIRECT, ["ssh", "sftp"]), (DIRECT, ["sftp", "upload"])])
    assert got == ["ssh", "sftp", "upload"]


def test_direct_rule_shadows_a_broader_group_grant():
    """The security property. A narrow direct rule (rank 0) must NOT pick up the
    broader group grant's (rank 2) actions — otherwise an intentional restriction
    is silently widened. Alice: direct {ssh} + ops-group {ssh,sftp,upload,download}
    → stays {ssh}."""
    got = UNION([(DIRECT, ["ssh"]), (GROUP, ["ssh", "sftp", "upload", "download"])])
    assert got == ["ssh"], "the group grant must be shadowed by the direct rule, not unioned in"


def test_peers_at_the_group_tier_also_union():
    """When the best rank IS the group tier (no direct rule), group peers combine —
    consistent behaviour at every tier."""
    got = UNION([(GROUP, ["ssh"]), (GROUP, ["sftp"])])
    assert set(got) == {"ssh", "sftp"}


# ── the resolver actually uses the helper and no longer takes a single row ──

def test_resolver_unions_and_dropped_the_early_break():
    src = INTERNAL.read_text()
    body = src[src.index("async def authz_resolve"):]
    body = body[: body.index("\n@router")]
    assert "_union_best_rank_actions(" in body, "authz_resolve must union, not take one row's actions"
    # the old early break stopped at the first rank-0 row, hiding same-rank peers
    assert "break  # direct host match" not in body, "the early break would hide same-rank peers from the union"
    # credential still comes from a single best row (you connect with one credential)
    assert "cred_ids = list(best.credential_ids" in body
