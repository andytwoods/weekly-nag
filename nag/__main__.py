"""CLI: ``nag daemon`` | ``nag settings`` | ``nag status`` | ``nag add`` | ``nag done``.

``status``, ``add`` and ``done`` need no GTK, so they work over SSH.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from .core import Urgency, evaluate
from .store import Store


def cmd_status(args) -> int:
    store = Store(args.db)
    now = dt.datetime.now()
    tasks = store.tasks()
    if not tasks:
        print("No tasks. Add one:  nag add Physio 4")
        return 0
    print(f"Week starting {now.date() - dt.timedelta(days=now.weekday())} (resets Mon 04:00)")
    for task in tasks:
        st = evaluate(task, store.credited_days(task, now), now)
        when = "" if task.days is None else f"  on {task.days_label}"
        if st.urgency is Urgency.WAITING:
            tail = f"{st.remaining} left, next chance {task.days_label}"
        else:
            tail = f"{st.remaining} left over {st.days_left}d"
        print(
            f"  {task.name:<24} {st.pips():<18} "
            f"{st.done_this_week}/{task.per_week}  "
            f"{tail}  [{st.urgency.value}]{when}"
        )
    return 0


def cmd_add(args) -> int:
    store = Store(args.db)
    try:
        store.add_task(args.name, args.per_week, args.max_per_day, args.days)
    except ValueError as exc:
        print(f"nag: {exc}", file=sys.stderr)
        return 1
    on = "" if not args.days else f" on {args.days}"
    print(f"Added {args.name}: {args.per_week}x/week{on}")
    return cmd_status(args)


def cmd_done(args) -> int:
    store = Store(args.db)
    matches = [t for t in store.tasks() if args.name.lower() in t.name.lower()]
    if len(matches) != 1:
        print(f"{'No' if not matches else 'Ambiguous'} task matching {args.name!r}", file=sys.stderr)
        return 1
    store.mark_done(matches[0])
    print(f"Marked {matches[0].name} done")
    return cmd_status(args)


def cmd_history(args) -> int:
    store = Store(args.db)
    for row in store.past_weeks():
        flag = "ok " if row["done"] >= row["target"] else "   "
        print(f"{row['week_start']}  {flag} {row['task']:<24} {row['done']}/{row['target']}")
    return 0


def cmd_daemon(args) -> int:
    from .daemon import main as daemon_main

    return daemon_main()


def cmd_settings(args) -> int:
    from .settings_ui import main as settings_main

    return settings_main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nag", description="Weekly-quota reminders.")
    p.add_argument("--db", default=None, help="override the SQLite path")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("daemon", help="run the resident reminder daemon").set_defaults(fn=cmd_daemon)
    sub.add_parser("settings", help="open the parent settings window").set_defaults(fn=cmd_settings)
    sub.add_parser("status", help="print this week's progress").set_defaults(fn=cmd_status)
    sub.add_parser("history", help="print past weeks").set_defaults(fn=cmd_history)

    a = sub.add_parser("add", help="add a task")
    a.add_argument("name")
    a.add_argument("per_week", type=int)
    a.add_argument("--max-per-day", dest="max_per_day", type=int, default=1)
    a.add_argument(
        "--days",
        default=None,
        metavar="mon,tue,...",
        help="restrict to certain weekdays, e.g. --days sat,sun (default: any day)",
    )
    a.set_defaults(fn=cmd_add)

    d = sub.add_parser("done", help="credit a task from the command line")
    d.add_argument("name")
    d.set_defaults(fn=cmd_done)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
