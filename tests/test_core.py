"""Tests for the scheduling logic. Pure Python -- no GTK, runs anywhere."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nag.core import (  # noqa: E402
    Task,
    Urgency,
    credited_date,
    days_left_in_week,
    evaluate,
    pending,
    reminder_interval,
    snooze_lock_seconds,
    week_start_date,
)

MON = dt.datetime(2026, 11, 2, 18, 0)   # a Monday evening
WED = dt.datetime(2026, 11, 4, 18, 0)
SAT = dt.datetime(2026, 11, 7, 18, 0)
SUN = dt.datetime(2026, 11, 8, 18, 0)
LATE_SUN = dt.datetime(2026, 11, 9, 1, 30)   # 01:30 Monday == still Sunday


def test_late_night_counts_as_the_previous_day():
    assert credited_date(LATE_SUN) == dt.date(2026, 11, 8)
    assert week_start_date(LATE_SUN) == dt.date(2026, 11, 2)
    assert days_left_in_week(LATE_SUN) == 1


def test_week_boundary_is_monday_0400():
    assert week_start_date(MON) == dt.date(2026, 11, 2)
    assert week_start_date(dt.datetime(2026, 11, 2, 3, 0)) == dt.date(2026, 10, 26)


def test_days_left_counts_today():
    assert days_left_in_week(MON) == 7
    assert days_left_in_week(SUN) == 1


def test_fresh_monday_with_a_light_quota_is_slack():
    st = evaluate(Task(1, "Physio", per_week=2), [], MON)
    assert st.urgency is Urgency.SLACK
    assert st.allow_not_today and not st.centre_screen


def test_four_a_week_on_monday_is_pace():
    st = evaluate(Task(1, "Physio", per_week=4), [], MON)
    assert st.urgency is Urgency.PACE


def test_sunday_with_one_left_is_must_and_withdraws_not_today():
    st = evaluate(Task(1, "Physio", per_week=4), [dt.date(2026, 11, 2)] * 3, SUN)
    assert st.urgency is Urgency.MUST
    assert not st.allow_not_today
    assert st.centre_screen


def test_quota_met_goes_silent():
    st = evaluate(Task(1, "Physio", per_week=3), [dt.date(2026, 11, 2)] * 3, WED)
    assert st.urgency is Urgency.DONE
    assert not st.urgency.nags
    assert st.remaining == 0


def test_one_credit_per_day_stops_binging():
    """Four sessions cannot be crammed into Sunday."""
    st = evaluate(Task(1, "Physio", per_week=4), [dt.date(2026, 11, 8)], SUN)
    assert st.done_today == 1
    assert st.urgency is Urgency.TODAY_DONE
    assert not st.urgency.nags


def test_max_per_day_above_one_allows_a_second_go_today():
    """done_today=1 of 2 allowed, so it still nags for the second one."""
    task = Task(1, "Inhaler", per_week=10, max_per_day=2)
    st = evaluate(task, [dt.date(2026, 11, 4)], WED)
    assert st.done_today == 1
    assert st.urgency is Urgency.PACE     # 9 left, 5 days x 2 = capacity 10 -> 0.9
    assert st.urgency.nags


def test_max_per_day_capacity_can_make_a_quota_unreachable():
    """9 left with 3 days x 2 doses = capacity 6, so the week is already gone."""
    task = Task(1, "Inhaler", per_week=10, max_per_day=2)
    st = evaluate(task, [dt.date(2026, 11, 2)], dt.datetime(2026, 11, 6, 18, 0))
    assert st.days_left == 3
    assert st.urgency is Urgency.MISSED


def test_max_per_day_capacity_exactly_met_is_must():
    """6 left with 3 days x 2 doses = capacity 6: both doses needed every day."""
    task = Task(1, "Inhaler", per_week=10, max_per_day=2)
    days = [dt.date(2026, 11, 2)] * 2 + [dt.date(2026, 11, 3)] * 2
    st = evaluate(task, days, dt.datetime(2026, 11, 6, 18, 0))
    assert st.remaining == 6 and st.days_left == 3
    assert st.urgency is Urgency.MUST
    assert not st.allow_not_today


def test_second_dose_done_goes_quiet_for_the_day():
    task = Task(1, "Inhaler", per_week=10, max_per_day=2)
    st = evaluate(task, [dt.date(2026, 11, 4)] * 2, WED)
    assert st.done_today == 2
    assert st.urgency is Urgency.TODAY_DONE


def test_impossible_quota_is_rejected_at_construction():
    with pytest.raises(ValueError):
        Task(1, "Nope", per_week=9)


def test_pips_render_progress():
    st = evaluate(Task(1, "Physio", per_week=4), [dt.date(2026, 11, 2)] * 2, WED)
    assert st.pips() == "● ● ○ ○"


def test_frequency_ramps_but_slack_only_asks_once():
    assert reminder_interval(Urgency.SLACK, 0) is None
    assert reminder_interval(Urgency.PACE, 0) == dt.timedelta(minutes=90)
    assert reminder_interval(Urgency.PACE, 4) is None          # bounded
    assert reminder_interval(Urgency.MUST, 0) == dt.timedelta(minutes=30)
    assert reminder_interval(Urgency.MUST, 3) == dt.timedelta(minutes=15)
    assert reminder_interval(Urgency.DONE, 0) is None


def test_snooze_lock_only_bites_on_a_must_task_already_brushed_off():
    assert snooze_lock_seconds(Urgency.MUST, 0) == 0
    assert snooze_lock_seconds(Urgency.MUST, 3) == 5
    assert snooze_lock_seconds(Urgency.PACE, 9) == 0


def test_pending_sorts_worst_first_and_drops_the_satisfied():
    must = evaluate(Task(1, "Physio", per_week=4), [dt.date(2026, 11, 2)] * 3, SUN)
    slack = evaluate(Task(2, "Guitar", per_week=1), [], MON)
    done = evaluate(Task(3, "Fish", per_week=1), [dt.date(2026, 11, 2)], MON)
    got = pending([slack, done, must])
    assert [s.task.id for s in got] == [1, 2]


def test_unreachable_quota_backs_off_instead_of_nagging_harder():
    """7x/week with 6 left and 2 days to go cannot be hit -- don't hammer it."""
    task = Task(1, "Feed Nibbles", per_week=7)
    st = evaluate(task, [dt.date(2026, 11, 2)], SAT)
    assert st.days_left == 2 and st.remaining == 6
    assert st.urgency is Urgency.MISSED
    assert st.urgency.nags                      # still asked, once a day
    assert reminder_interval(st.urgency, 0) is None
    assert st.allow_not_today                   # "Not today" comes back
    assert not st.centre_screen                 # and it never takes the screen
    assert snooze_lock_seconds(st.urgency, 9) == 0


def test_exactly_on_the_line_is_must_not_missed():
    """2 left, 2 days: today still decides the week."""
    st = evaluate(Task(1, "Physio", per_week=4), [dt.date(2026, 11, 2)] * 2, SAT)
    assert st.remaining == 2 and st.days_left == 2
    assert st.urgency is Urgency.MUST


def test_a_daily_task_is_must_every_day_from_monday():
    st = evaluate(Task(1, "Feed Nibbles", per_week=7), [], MON)
    assert st.urgency is Urgency.MUST


def test_missed_sorts_below_everything_that_can_still_be_saved():
    missed = evaluate(Task(1, "Nibbles", per_week=7), [], SAT)
    must = evaluate(Task(2, "Physio", per_week=2), [], SAT)
    assert [s.task.id for s in pending([missed, must])] == [2, 1]


# --------------------------------------------------------------------------- #
# Weekday restrictions -- "once a week, but on Saturday or Sunday"
# --------------------------------------------------------------------------- #

from nag.core import eligible_days_left, format_days, parse_days  # noqa: E402

WEEKEND = parse_days("sat,sun")


def test_parse_days_accepts_names_abbreviations_and_numbers():
    assert parse_days("sat,sun") == frozenset({5, 6})
    assert parse_days("Saturday, Sunday") == frozenset({5, 6})
    assert parse_days("5,6") == frozenset({5, 6})
    assert parse_days("") is None and parse_days(None) is None


def test_parse_days_rejects_rubbish():
    for bad in ("funday", "9", "sat,nope"):
        with pytest.raises(ValueError):
            parse_days(bad)


def test_format_days_round_trips():
    assert format_days(WEEKEND) == "sat,sun"
    assert format_days(None) == "any day"


def test_eligible_days_counts_only_the_allowed_ones():
    assert eligible_days_left(MON, WEEKEND) == 2
    assert eligible_days_left(SAT, WEEKEND) == 2
    assert eligible_days_left(SUN, WEEKEND) == 1
    assert eligible_days_left(MON, None) == 7


def test_weekend_task_is_silent_on_a_weekday():
    task = Task(1, "Rubbish", per_week=1, days=WEEKEND)
    for day in (MON, WED):
        st = evaluate(task, [], day)
        assert st.urgency is Urgency.WAITING
        assert not st.urgency.nags


def test_weekend_task_nudges_on_saturday_and_insists_on_sunday():
    task = Task(1, "Rubbish", per_week=1, days=WEEKEND)
    assert evaluate(task, [], SAT).urgency is Urgency.SLACK
    sun = evaluate(task, [], SUN)
    assert sun.urgency is Urgency.MUST
    assert not sun.allow_not_today and sun.centre_screen


def test_doing_it_saturday_silences_sunday():
    task = Task(1, "Rubbish", per_week=1, days=WEEKEND)
    assert evaluate(task, [dt.date(2026, 11, 7)], SUN).urgency is Urgency.DONE


def test_urgency_is_measured_against_allowed_days_not_the_calendar():
    """Two sessions, weekends only: on Monday that is already both weekend days."""
    task = Task(1, "Swim", per_week=2, days=WEEKEND)
    st = evaluate(task, [], MON)
    assert st.days_left == 2                      # not 7
    assert st.urgency is Urgency.WAITING          # silent until Saturday
    assert evaluate(task, [], SAT).urgency is Urgency.MUST   # both days needed


def test_quota_that_cannot_fit_its_days_is_rejected():
    with pytest.raises(ValueError, match="impossible on sun"):
        Task(1, "Nope", per_week=3, days=parse_days("sun"))
    # ...but fits once two doses a day are allowed.
    Task(2, "Fine", per_week=3, max_per_day=2, days=parse_days("sun,sat"))


def test_allows_and_label():
    task = Task(1, "Rubbish", per_week=1, days=WEEKEND)
    assert task.allows(dt.date(2026, 11, 7)) and not task.allows(dt.date(2026, 11, 4))
    assert task.days_label == "sat,sun"
    assert Task(2, "Any", per_week=1).days_label == "any day"
