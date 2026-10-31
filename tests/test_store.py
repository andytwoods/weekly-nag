"""Tests for the SQLite store and the daemon's due-now decision."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nag.core import Urgency, evaluate  # noqa: E402
from nag.daemon import due_now  # noqa: E402
from nag.store import Store  # noqa: E402

MON = dt.datetime(2026, 11, 2, 18, 0)
SUN = dt.datetime(2026, 11, 8, 18, 0)


def fresh() -> Store:
    return Store(":memory:")


def test_completion_counts_toward_the_right_week():
    s = fresh()
    tid = s.add_task("Physio", 4)
    task = s.tasks()[0]
    s.mark_done(task, MON)
    assert s.credited_days(task, MON) == [dt.date(2026, 11, 2)]
    assert s.credited_days(task, MON + dt.timedelta(days=7)) == []   # nothing rolls over
    assert tid == task.id


def test_undo_removes_the_credit_without_deleting_history():
    s = fresh()
    s.add_task("Physio", 4)
    task = s.tasks()[0]
    cid = s.mark_done(task, MON)
    s.undo(cid, MON)
    assert s.credited_days(task, MON) == []
    assert s.db.execute("SELECT COUNT(*) FROM completion").fetchone()[0] == 1


def test_target_is_frozen_at_completion_time():
    s = fresh()
    s.add_task("Physio", 4)
    task = s.tasks()[0]
    s.mark_done(task, MON)
    s.update_task(task.id, per_week=7)
    row = s.db.execute("SELECT target_at_time FROM completion").fetchone()
    assert row["target_at_time"] == 4


def test_warmup_silences_the_first_prompt_after_login():
    s = fresh()
    s.add_task("Physio", 4)
    assert due_now(s, MON, session_start=MON - dt.timedelta(seconds=60)) == []
    got = due_now(s, MON, session_start=MON - dt.timedelta(hours=2))
    assert [x.task.name for x in got] == ["Physio"]


def test_not_today_silences_that_task_for_the_day():
    s = fresh()
    s.add_task("Guitar", 2)
    task = s.tasks()[0]
    early = MON - dt.timedelta(hours=2)
    assert due_now(s, MON, early)
    s.log(task.id, "not_today", MON)
    assert due_now(s, MON, early) == []
    tomorrow = MON + dt.timedelta(days=1)
    assert due_now(s, tomorrow, tomorrow - dt.timedelta(hours=2))


def test_slack_task_is_shown_once_a_day_only():
    s = fresh()
    s.add_task("Guitar", 1)
    task = s.tasks()[0]
    early = MON - dt.timedelta(hours=2)
    assert evaluate(task, [], MON).urgency is Urgency.SLACK
    s.log(task.id, "shown", MON)
    s.log(None, "round", MON)
    assert due_now(s, MON + dt.timedelta(hours=3), early) == []


def test_must_task_returns_after_thirty_minutes():
    s = fresh()
    s.add_task("Physio", 4)
    task = s.tasks()[0]
    for day in (2, 3, 4):
        s.mark_done(task, dt.datetime(2026, 11, day, 18, 0))
    early = SUN - dt.timedelta(hours=4)
    assert evaluate(task, s.credited_days(task, SUN), SUN).urgency is Urgency.MUST
    s.log(task.id, "shown", SUN)
    s.log(None, "round", SUN)
    assert due_now(s, SUN + dt.timedelta(minutes=10), early) == []
    assert due_now(s, SUN + dt.timedelta(minutes=31), early)


def test_daily_round_cap_stops_the_nagging():
    s = fresh()
    s.add_task("Physio", 7)
    task = s.tasks()[0]
    early = MON - dt.timedelta(hours=2)
    for i in range(8):
        s.log(None, "round", MON)
        s.log(task.id, "shown", MON)
    assert due_now(s, MON + dt.timedelta(hours=6), early) == []


def test_suppress_until_honoured():
    s = fresh()
    s.add_task("Physio", 4)
    early = MON - dt.timedelta(hours=2)
    assert due_now(s, MON, early, suppress_until=MON + dt.timedelta(minutes=5)) == []


def test_retired_task_stops_nagging_but_keeps_its_rows():
    s = fresh()
    s.add_task("Old thing", 3)
    task = s.tasks()[0]
    s.mark_done(task, MON)
    s.retire_task(task.id)
    assert s.tasks() == []
    assert len(s.tasks(active_only=False)) == 1
    assert due_now(s, MON, MON - dt.timedelta(hours=2)) == []
