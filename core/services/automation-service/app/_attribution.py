"""Who a job run is attributed to, from its ``triggered_by`` marker.

Kept dependency-free so the test suite loads this exact file (see
tests/test_job_attribution.py) instead of a hand-copied mirror. The mirror it
replaces tested bare UUIDs, while every real run stores a prefixed marker, so it
passed while production attribution was broken.

``triggered_by`` forms in use:
  * ``user:<user_id>``                       — a person ran or re-ran it
  * ``manual_trigger:<binding_id>:<user_id>`` — a person fired a trigger binding
  * ``schedule:<id>``, ``zabbix_event:<id>``, ``zabbix:<id>``, ``system:<task>``
                                              — machine-originated
  * ``zabbix_manual:<event_id>``               — a Zabbix operator ran a script; no
                                                SeyalRun identity is known, so it is
                                                attributed like a machine trigger
  * a bare ``<user_id>``                      — rows written before the prefixes
"""

from __future__ import annotations


def actor_user_id(triggered_by: str | None) -> str | None:
    """The user id to put in an audit row's ``user_id``, or None for a machine trigger.

    Passing the raw marker through wrote ``user:<uuid>`` (41 chars) into
    za_audit_logs.user_id, a String(36) column, so identity-service rejected the
    row and the forwarder dropped it: user-triggered job.start/job.finish audit
    entries were never stored on Postgres. Machine markers such as
    ``zabbix_event:9`` would also have been recorded as if they were accounts.
    """
    if not triggered_by:
        return None
    kind, sep, rest = triggered_by.partition(":")
    if not sep:
        return triggered_by
    if kind == "user":
        return rest or None
    if kind == "manual_trigger":
        _binding, _, user = rest.partition(":")
        return user or None
    return None


# Run param carrying the people who configured a machine trigger (a schedule's
# or Zabbix binding's author and last editor). Set by the scheduler / internal
# endpoint AFTER caller params are merged, so a caller cannot clear it.
ACCOUNTABLE_PARAM = "_accountable_user_ids"


def accountable_ids(*user_ids: str | None) -> list[str]:
    """De-duplicated, non-empty ids, in order."""
    out: list[str] = []
    for u in user_ids:
        if u and u not in out:
            out.append(u)
    return out


def with_editor(editor_ids: list | None, user_id: str) -> list[str]:
    """editor_ids with user_id appended once — never removes anyone. A single
    last-editor field would let a later, unrelated edit free the person who made
    the risky change to approve it."""
    ids = list(editor_ids or [])
    if user_id and user_id not in ids:
        ids.append(user_id)
    return ids


def is_requester(triggered_by: str | None, user_id: str | None,
                 accountable: list[str] | None = None) -> bool:
    """Is ``user_id`` responsible for the run, and so barred from approving it (F-2)?

    True for the person who triggered it, and for anyone accountable for the
    machine trigger that produced it — a scheduled run's ``triggered_by`` names
    the schedule, not the admin who set the schedule up, so without
    ``accountable`` that admin could approve their own change.

    Fails closed: with no caller identity we cannot prove the approver is a
    different person, so the caller is treated as the requester."""
    if not user_id:
        return True
    if user_id in (accountable or []):
        return True
    return actor_user_id(triggered_by) == user_id
