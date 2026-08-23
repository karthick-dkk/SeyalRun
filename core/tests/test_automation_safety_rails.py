"""Concurrency, schedule timezones, and alerts for runs that stop making progress.

Three gaps found reviewing the automation engine. None is a missing feature —
the engine already has retry, tightening-only timeouts, cancel, approvals and
chains. All three are safety rails around execution:

  * nothing stopped a template running concurrently with itself. For a report
    that is waste; for rotate_secret it is two processes racing the same account,
    with the vault recording one outcome and the host keeping the other — the
    credential then no longer opens the host it belongs to.

  * cron was evaluated in UTC, so "0 2 * * *" meant 2am UTC: the wrong hour for
    almost everyone, and it shifted by an hour twice a year because UTC has no
    DST while the expectation behind "run at 2am" does.

  * completion was the only notified event, so an approval nobody actioned and a
    run that outlived its timeout produced no signal at all — exactly backwards,
    since the runs worth interrupting someone for are the ones not progressing.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUTO = ROOT / "services/automation-service/app"
CONC = AUTO / "_concurrency.py"
SCHED = AUTO / "scheduler.py"
TEMPLATES = AUTO / "api/job_templates.py"
MODELS = AUTO / "models.py"
MIGRATIONS = AUTO.parent / "migrations/versions"


def test_modules_exist():
    """Guard against every assertion below passing vacuously."""
    assert CONC.exists()
    assert "_local_now" in SCHED.read_text()


# ── concurrency ──────────────────────────────────────────────────────────────

def test_live_statuses_include_everything_that_owns_the_hosts():
    src = CONC.read_text()
    m = re.search(r"LIVE_STATUSES = \(([^)]*)\)", src)
    assert m, "LIVE_STATUSES not found"
    live = set(re.findall(r'"(\w+)"', m.group(1)))
    assert {"pending", "running"} <= live
    assert "pending_approval" in live, (
        "a run awaiting approval still owns its hosts — starting a second one "
        "means the approver releases work into a race"
    )


def test_guard_queries_the_database_not_an_in_process_lock():
    """This service can run more than one worker, and a lock in one says nothing
    about the others. The row is the shared state."""
    src = CONC.read_text()
    assert "select(ZAJobRun.id)" in src
    for bad in ("threading.Lock", "asyncio.Lock", "_locks = {"):
        assert bad not in src, f"in-process locking ({bad}) does not survive a second worker"


def test_manual_run_refuses_when_one_is_already_live():
    src = TEMPLATES.read_text()
    assert "live_run_id(session, template_id)" in src
    block = src[src.index("live_run_id(session, template_id)"):][:600]
    assert "HTTP_409_CONFLICT" in block, "a double-trigger must be refused, not silently queued"
    assert "already running" in block, "the message must name what is already happening"


def test_guard_runs_before_the_run_row_is_created():
    """Creating the row first and checking after would leave an orphan run."""
    src = TEMPLATES.read_text()
    assert src.index("live_run_id(session, template_id)") < src.index("run = ZAJobRun(")


def test_scheduled_overrun_is_skipped_and_the_cron_still_advances():
    """The overrun case this exists for. Skipping without advancing the cursor
    would make the schedule fire again immediately and keep re-skipping."""
    src = SCHED.read_text()
    block = src[src.index("busy = await live_run_id(session, sched.job_template_id)"):][:900]
    assert "continue" in block
    assert "next_run_at" in block, "the cron cursor must advance even when a tick is skipped"
    assert "last_run_at" in block


# ── schedule timezones ───────────────────────────────────────────────────────

def _local_now():
    lines = SCHED.read_text().split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("def _local_now"))
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        end += 1
    ns: dict = {}
    try:
        from zoneinfo import ZoneInfo
        ns["ZoneInfo"] = ZoneInfo
    except ImportError:                      # pragma: no cover
        ns["ZoneInfo"] = None
    exec(compile("\n".join(lines[start:end]), "<sched>", "exec"), ns)
    return ns["_local_now"]


class _Sched:
    def __init__(self, tz): self.timezone = tz


@pytest.mark.parametrize("tz,expect_hour", [
    ("UTC", 1),
    ("Asia/Kolkata", 6),        # +5:30
    ("America/New_York", 21),   # -4 in August
])
def test_cron_is_evaluated_in_the_schedules_own_zone(tz: str, expect_hour: int):
    now = dt.datetime(2026, 8, 23, 1, 0, tzinfo=dt.timezone.utc)
    assert _local_now()(_Sched(tz), now).hour == expect_hour


def test_the_same_wall_clock_hour_holds_across_a_dst_transition():
    """The point of the feature: "run at 2am" must stay 2am in January and July,
    not drift by an hour when the offset changes."""
    fn = _local_now()
    winter = fn(_Sched("America/New_York"), dt.datetime(2026, 1, 15, 7, 0, tzinfo=dt.timezone.utc))
    summer = fn(_Sched("America/New_York"), dt.datetime(2026, 7, 15, 6, 0, tzinfo=dt.timezone.utc))
    assert winter.hour == summer.hour == 2, (winter, summer)
    assert winter.utcoffset() != summer.utcoffset(), "expected different offsets either side of DST"


@pytest.mark.parametrize("tz", [None, "", "Not/AZone", "Mars/Olympus"])
def test_unknown_or_missing_zone_falls_back_to_utc(tz):
    """Existing schedules have no zone. Falling back to UTC is the behaviour they
    already had, so a deploy does not move anyone's nightly job to a new hour."""
    now = dt.datetime(2026, 8, 23, 1, 0, tzinfo=dt.timezone.utc)
    assert _local_now()(_Sched(tz), now).hour == 1


def test_next_run_is_stored_back_in_utc():
    """The column is timestamptz and every comparison in the loop is against a UTC
    now; storing a local-zone value would make the schedule fire at the offset."""
    src = SCHED.read_text()
    assert "astimezone(timezone.utc)" in src


def test_timezone_column_exists_and_defaults_to_utc():
    model = MODELS.read_text()
    m = re.search(r'timezone: Mapped\[str\] = mapped_column\(String\(\d+\), nullable=False, default="([^"]+)"\)', model)
    assert m and m.group(1) == "UTC"
    mig = sorted(MIGRATIONS.glob("*_schedule_timezone.py"))
    assert mig, "no migration adds the timezone column"
    src = mig[-1].read_text()
    assert 'server_default="UTC"' in src, "existing rows need a default or the column is NOT NULL-violating"


# ── stalled-run alerts ───────────────────────────────────────────────────────

def test_stall_check_covers_both_stuck_and_unapproved():
    src = SCHED.read_text()
    fn = src[src.index("async def _alert_stalled_runs"):]
    fn = fn[: fn.index("\nasync def scheduler_loop")]
    assert '"pending_approval"' in fn and '"running"' in fn
    assert "job_exec_timeout_seconds" in fn, "'stuck' must be relative to the run's own ceiling"


def test_stall_alert_fires_once_per_run():
    """Re-alerting every tick trains people to ignore the notification."""
    src = SCHED.read_text()
    fn = src[src.index("async def _alert_stalled_runs"):]
    fn = fn[: fn.index("\nasync def scheduler_loop")]
    assert "_stall_notified" in fn
    assert "if params.get(\"_stall_notified\")" in fn


def test_stall_check_cannot_stop_schedules_firing():
    """A missed alert is bad; a stopped scheduler is worse."""
    src = SCHED.read_text()
    block = src[src.index("await _alert_stalled_runs(session, now)") - 200:][:400]
    assert "try:" in block and "except Exception" in block
