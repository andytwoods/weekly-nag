"""Pure scheduling logic for the weekly-quota reminder.

No GTK, no SQLite, no clock reads except the ``now`` you pass in -- so all of this
is unit-testable on any machine, including the Mac it was written on.

The model (agreed across GPT, Gemini and Qwen3.5 on 2026-10-31):

* A task is "do this N times a week". It can optionally be restricted to
  certain weekdays ("once a week, on Saturday or Sunday"), which is what a
  real-world deadline like bin collection needs -- a free-floating weekly
  quota would happily let the bins go out on Wednesday.
* The logical week runs Monday 04:00 -> Monday 04:00 local. Teenagers are awake
  past midnight, so a 01:30 completion counts for the previous day.
* Nothing rolls over. Monday wipes the slate; the old week is kept as history.
* At most ``max_per_day`` credits per task per day, so four physio sessions
  cannot be binged on Sunday.
* How *often* we prompt scales with how far behind he is. How *loud* we prompt
  does not -- the one escalation is moving to the centre of the screen once the
  task is mathematically must-do-today.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

#: The logical day (and week) boundary, in local time.
WEEK_START_HOUR = 4

#: Hard ceiling on reminder rounds per day, across all tasks. Past this we stop
#: and leave a quiet tray indicator -- the single most important anti-rage knob.
MAX_ROUNDS_PER_DAY = 8


#: Monday-first weekday names, as accepted on the command line.
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_days(spec: str | None) -> frozenset[int] | None:
    """``"sat,sun"`` -> ``frozenset({5, 6})``. Blank or ``None`` means any day."""
    if not spec or not spec.strip():
        return None
    out = set()
    for part in spec.replace(" ", "").lower().split(","):
        if not part:
            continue
        if part.isdigit():
            n = int(part)
        elif part[:3] in DAY_NAMES:
            n = DAY_NAMES.index(part[:3])
        else:
            raise ValueError(f"not a weekday: {part!r}")
        if not 0 <= n <= 6:
            raise ValueError(f"weekday out of range: {part!r}")
        out.add(n)
    return frozenset(out) or None


def format_days(days: frozenset[int] | None) -> str:
    """``frozenset({5, 6})`` -> ``"sat,sun"``. ``None`` -> ``"any day"``."""
    if not days:
        return "any day"
    return ",".join(DAY_NAMES[d] for d in sorted(days))


class Urgency(str, Enum):
    """How much attention a task deserves right now."""

    DONE = "done"              #: weekly quota met; silent until next Monday
    TODAY_DONE = "today_done"  #: already credited today; silent until tomorrow
    WAITING = "waiting"        #: not one of this task's days; silent
    SLACK = "slack"            #: comfortably ahead; one gentle prompt a day
    PACE = "pace"              #: needs doing on most remaining days
    MUST = "must"              #: the quota is unreachable unless it happens today
    MISSED = "missed"           #: the quota is already unreachable; back off, stay honest

    @property
    def nags(self) -> bool:
        return self in (Urgency.SLACK, Urgency.PACE, Urgency.MUST, Urgency.MISSED)


@dataclass(frozen=True)
class Task:
    """A thing Robin has to do N times a week."""

    id: int
    name: str
    per_week: int
    max_per_day: int = 1
    active: bool = True
    #: Weekdays this task may be done on (0=Mon .. 6=Sun). ``None`` = any day.
    days: frozenset[int] | None = None

    def __post_init__(self) -> None:
        slots = len(self.days) if self.days else 7
        if self.per_week < 1 or self.per_week > slots * max(self.max_per_day, 1):
            raise ValueError(
                f"per_week={self.per_week} is impossible on {format_days(self.days)} "
                f"with max_per_day={self.max_per_day}"
            )

    def allows(self, day: dt.date) -> bool:
        return self.days is None or day.weekday() in self.days

    @property
    def days_label(self) -> str:
        return format_days(self.days)


@dataclass(frozen=True)
class TaskState:
    """A task plus everything the UI needs to draw and decide."""

    task: Task
    done_this_week: int
    done_today: int
    days_left: int
    urgency: Urgency

    @property
    def remaining(self) -> int:
        return max(self.task.per_week - self.done_this_week, 0)

    @property
    def allow_not_today(self) -> bool:
        """"Not today" is withdrawn only on the day that still decides the week."""
        return self.urgency is not Urgency.MUST

    @property
    def centre_screen(self) -> bool:
        return self.urgency is Urgency.MUST

    def pips(self) -> str:
        """``● ● ○ ○`` -- progress at a glance, with no fragile daily streak."""
        return " ".join(["●"] * self.done_this_week + ["○"] * self.remaining)


# --------------------------------------------------------------------------- #
# Calendar helpers
# --------------------------------------------------------------------------- #

def credited_date(when: dt.datetime, week_start_hour: int = WEEK_START_HOUR) -> dt.date:
    """The logical day an event at ``when`` counts for."""
    if when.hour < week_start_hour:
        return (when - dt.timedelta(days=1)).date()
    return when.date()


def week_start_date(when: dt.datetime, week_start_hour: int = WEEK_START_HOUR) -> dt.date:
    """The Monday of ``when``'s logical week."""
    day = credited_date(when, week_start_hour)
    return day - dt.timedelta(days=day.weekday())


def week_start_dt(when: dt.datetime, week_start_hour: int = WEEK_START_HOUR) -> dt.datetime:
    """The exact instant ``when``'s logical week began (Monday 04:00)."""
    return dt.datetime.combine(
        week_start_date(when, week_start_hour), dt.time(hour=week_start_hour)
    )


def days_left_in_week(when: dt.datetime, week_start_hour: int = WEEK_START_HOUR) -> int:
    """Days remaining in the logical week, today included. Mon -> 7, Sun -> 1."""
    return 7 - credited_date(when, week_start_hour).weekday()


def eligible_days_left(
    when: dt.datetime,
    days: frozenset[int] | None,
    week_start_hour: int = WEEK_START_HOUR,
) -> int:
    """Of the days left in the week, how many is this task allowed on?

    A task limited to Saturday and Sunday has two chances left on Monday and
    one on Saturday, so its urgency must be measured against those, not
    against the seven days that remain on the calendar.
    """
    today = credited_date(when, week_start_hour)
    if days is None:
        return days_left_in_week(when, week_start_hour)
    return sum(1 for d in range(today.weekday(), 7) if d in days)


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #

def evaluate(task: Task, credited_days: list[dt.date], now: dt.datetime) -> TaskState:
    """Work out where ``task`` stands.

    ``credited_days`` is one entry per completion event in the current logical
    week (repeats allowed, for tasks with ``max_per_day > 1``).
    """
    today = credited_date(now)
    done_week = len(credited_days)
    done_today = sum(1 for d in credited_days if d == today)
    left = eligible_days_left(now, task.days)
    remaining = max(task.per_week - done_week, 0)

    if not task.active or remaining == 0:
        urgency = Urgency.DONE
    elif done_today >= task.max_per_day:
        urgency = Urgency.TODAY_DONE
    elif not task.allows(today):
        # Not one of this task's days. Stay silent rather than nagging about
        # something that cannot usefully be done yet.
        urgency = Urgency.WAITING
    else:
        # How many of the remaining days must carry a session?
        capacity = left * task.max_per_day
        if remaining > capacity:
            # Already out of reach. Nagging hard for a target that cannot be hit
            # is the fastest way to teach reflex-dismissal, so drop to one
            # honest prompt a day and let the week close.
            urgency = Urgency.MISSED
        elif remaining == capacity:
            urgency = Urgency.MUST
        elif remaining / capacity > 0.5:
            urgency = Urgency.PACE
        else:
            urgency = Urgency.SLACK

    return TaskState(
        task=task,
        done_this_week=done_week,
        done_today=done_today,
        days_left=left,
        urgency=urgency,
    )


def reminder_interval(urgency: Urgency, snoozes_today: int) -> dt.timedelta | None:
    """How long to wait before showing this again.

    ``None`` means "not again today". Frequency ramps; intensity does not.
    """
    if urgency in (Urgency.SLACK, Urgency.MISSED):
        return None                                   # one gentle prompt a day
    if urgency is Urgency.PACE:
        if snoozes_today >= 4:
            return None                               # bounded, per GPT
        return dt.timedelta(minutes=90)
    if urgency is Urgency.MUST:
        return dt.timedelta(minutes=15 if snoozes_today >= 3 else 30)
    return None


def snooze_lock_seconds(urgency: Urgency, snoozes_today: int) -> int:
    """Seconds the snooze buttons stay greyed, forcing a beat of attention.

    Only ever applied once a task is must-do-today *and* has already been
    brushed off three times. Never a puzzle, never a captcha.
    """
    if urgency is Urgency.MUST and snoozes_today >= 3:
        return 5
    return 0


def pending(states: list[TaskState]) -> list[TaskState]:
    """The tasks worth showing, worst first."""
    order = {Urgency.MUST: 0, Urgency.PACE: 1, Urgency.SLACK: 2, Urgency.MISSED: 3}
    live = [s for s in states if s.urgency.nags]
    return sorted(live, key=lambda s: (order[s.urgency], -s.remaining, s.task.name))
