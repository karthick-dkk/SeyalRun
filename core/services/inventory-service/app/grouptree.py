"""Host groups as a tree — pure functions over {group_id: parent_id}.

A host group may have a parent. A grant on a group covers the hosts of every
group below it, so the two questions here are the ones authorization depends on:
what lies under a group (``descendants``), and whether a proposed parent would
make the tree stop being a tree (``parent_error``).

Kept stdlib-only and DB-free on purpose: the callers load the whole group table
in ONE query (it is small — tens to hundreds of rows) and walk it in memory,
which is the same on PostgreSQL and MySQL and is never N round trips. The test
suite exercises these directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# Levels, counting the root as 1. Deep enough for site > room > rack > role, with
# room to spare; shallow enough that a runaway import cannot build a chain that
# makes every expansion slow.
MAX_DEPTH = 8


def children_map(parent_of: Mapping[str, str | None]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for gid, parent in parent_of.items():
        if parent:
            out.setdefault(parent, []).append(gid)
    return out


def descendants(parent_of: Mapping[str, str | None], roots: Iterable[str]) -> set[str]:
    """``roots`` plus every group below them. Cycle-safe: a corrupted table (a
    cycle written behind the API's back) terminates instead of looping, because a
    group is only ever expanded once."""
    kids = children_map(parent_of)
    seen: set[str] = set()
    stack = [r for r in roots if r]
    while stack:
        gid = stack.pop()
        if gid in seen:
            continue
        seen.add(gid)
        stack.extend(kids.get(gid, ()))
    return seen


def depth_of(parent_of: Mapping[str, str | None], gid: str) -> int:
    """1 for a root. Stops at a cycle rather than looping."""
    depth, seen, cur = 0, set(), gid
    while cur and cur not in seen:
        seen.add(cur)
        depth += 1
        cur = parent_of.get(cur)
    return depth


def subtree_height(parent_of: Mapping[str, str | None], gid: str) -> int:
    """Levels in the subtree rooted at ``gid`` (1 for a leaf)."""
    kids = children_map(parent_of)
    best, stack, seen = 0, [(gid, 1)], set()
    while stack:
        cur, h = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        best = max(best, h)
        stack.extend((k, h + 1) for k in kids.get(cur, ()))
    return best


def parent_error(parent_of: Mapping[str, str | None], group_id: str | None, new_parent: str | None) -> str | None:
    """Why ``group_id`` may not be placed under ``new_parent`` — or None if it may.

    ``group_id`` is None for a group being created (it has no subtree yet).
    """
    if not new_parent:
        return None
    if new_parent not in parent_of:
        return "parent group not found"
    if group_id and new_parent == group_id:
        return "a group cannot be its own parent"
    if group_id and new_parent in descendants(parent_of, [group_id]):
        return "that parent is inside this group's own subtree — it would create a cycle"
    height = subtree_height(parent_of, group_id) if group_id else 1
    if depth_of(parent_of, new_parent) + height > MAX_DEPTH:
        return f"the host tree may be at most {MAX_DEPTH} levels deep"
    return None
