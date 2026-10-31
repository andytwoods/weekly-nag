"""The resident daemon: decide, show, record. One process, started at login.

Started from ``~/.config/autostart`` rather than a ``systemd --user`` service:
all three consulted models flagged that user services race the X11 session and
come up without ``DISPLAY``/``DBUS_SESSION_BUS_ADDRESS``. A systemd *timer* is
still useful as a watchdog that restarts this if it dies -- see ``install.sh``.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import os
import sys
from pathlib import Path

from . import idle
from .core import MAX_ROUNDS_PER_DAY, TaskState, Urgency, evaluate, pending, reminder_interval
from .store import Store

TICK_SECONDS = 30


def states(store: Store, now: dt.datetime) -> list[TaskState]:
    return [evaluate(t, store.credited_days(t, now), now) for t in store.tasks()]


def due_now(
    store: Store,
    now: dt.datetime,
    session_start: dt.datetime,
    suppress_until: dt.datetime | None = None,
) -> list[TaskState]:
    """The tasks that have earned a prompt right now. Pure apart from reads."""
    if suppress_until and now < suppress_until:
        return []
    if store.count_today("round", now) >= MAX_ROUNDS_PER_DAY:
        return []

    warmup = dt.timedelta(seconds=store.get_int("warmup_s", 1200))
    if store.count_today("round", now) == 0 and now - session_start < warmup:
        return []

    out = []
    for st in pending(states(store, now)):
        tid = st.task.id
        if store.skipped_today(tid, now):
            continue
        shown = store.shown_today(tid, now)
        interval = reminder_interval(st.urgency, store.snoozes_today(tid, now))
        if shown == 0:
            out.append(st)
            continue
        if interval is None:
            continue                       # SLACK: one prompt a day, that's it
        last = store.last_shown_at(tid)
        if last is None or now - last >= interval:
            out.append(st)
    return out


class Daemon:
    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()
        self.session_start = dt.datetime.now()
        self.suppress_until: dt.datetime | None = None
        self.card = None

    # -- single instance --------------------------------------------------- #

    @staticmethod
    def claim_lock() -> object | None:
        base = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
        fh = open(base / "nag.lock", "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        fh.write(str(os.getpid()))
        fh.flush()
        return fh

    # -- the loop ---------------------------------------------------------- #

    def run(self) -> int:
        from gi.repository import GLib

        lock = self.claim_lock()
        if lock is None:
            print("nag: already running", file=sys.stderr)
            return 0
        self._lock = lock
        GLib.timeout_add_seconds(TICK_SECONDS, self._tick)
        GLib.timeout_add_seconds(2, self._tick)   # one quick look after login

        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        Gtk.main()
        return 0

    def _tick(self) -> bool:
        if self.card is not None:
            return True                      # a card is already on screen
        now = dt.datetime.now()
        if not idle.at_keyboard(self.store.get_int("idle_limit_s", 60)):
            return True                      # not at the machine; spend nothing
        due = due_now(self.store, now, self.session_start, self.suppress_until)
        if due:
            self._show(due, now)
        return True

    def _show(self, due: list[TaskState], now: dt.datetime) -> None:
        from .popup import ReminderCard

        snoozes = max(self.store.snoozes_today(s.task.id, now) for s in due)
        for st in due:
            self.store.log(st.task.id, "shown", now)
        self.store.log(None, "round", now)

        self.card = ReminderCard(
            states=due,
            snoozes_today=snoozes,
            on_done=self._done,
            on_snooze=self._snooze,
            on_not_today=self._not_today,
            on_dismiss=lambda: self._snooze(15),
            monitor_pref=self.store.get("monitor", "pointer"),
        )

    # -- outcomes ---------------------------------------------------------- #

    def _done(self, st: TaskState) -> None:
        from .popup import UndoToast

        self.card = None
        completion_id = self.store.mark_done(st.task)
        self.suppress_until = dt.datetime.now() + dt.timedelta(minutes=10)
        UndoToast(
            f"{st.task.name} marked done",
            on_undo=lambda: self.store.undo(completion_id),
        )

    def _snooze(self, minutes: int) -> None:
        self.card = None
        now = dt.datetime.now()
        self.suppress_until = now + dt.timedelta(minutes=minutes)
        for st in states(self.store, now):
            if st.urgency.nags:
                self.store.log(st.task.id, "snooze", now)

    def _not_today(self, shown: list[TaskState]) -> None:
        self.card = None
        now = dt.datetime.now()
        for st in shown:
            self.store.log(st.task.id, "not_today", now)
        self.suppress_until = now + dt.timedelta(minutes=30)


def main() -> int:
    return Daemon().run()
