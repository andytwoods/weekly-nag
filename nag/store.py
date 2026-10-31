"""SQLite store: tasks, settings and an append-only completion log.

One file, ``~/.local/share/nag/nag.db``. Completions are *events*, never a
counter, so corrections and undo are possible without rewriting history, and a
past week's result cannot be changed by editing a task's quota later.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path

from .core import Task, credited_date, format_days, parse_days, week_start_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    per_week    INTEGER NOT NULL,
    max_per_day INTEGER NOT NULL DEFAULT 1,
    active      INTEGER NOT NULL DEFAULT 1,
    days        TEXT    NOT NULL DEFAULT ''   -- '' = any day, else 'sat,sun'
);

CREATE TABLE IF NOT EXISTS completion (
    id             INTEGER PRIMARY KEY,
    task_id        INTEGER NOT NULL REFERENCES task(id),
    done_at        TEXT    NOT NULL,   -- ISO local timestamp of the click
    credited_date  TEXT    NOT NULL,   -- logical day it counts for (YYYY-MM-DD)
    week_start     TEXT    NOT NULL,   -- Monday of its logical week
    target_at_time INTEGER NOT NULL,   -- the quota as it stood, frozen
    undone         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS completion_week ON completion(task_id, week_start, undone);

CREATE TABLE IF NOT EXISTS interaction (
    id       INTEGER PRIMARY KEY,
    task_id  INTEGER,                  -- NULL = whole-card action
    at       TEXT    NOT NULL,
    day      TEXT    NOT NULL,
    action   TEXT    NOT NULL          -- shown | snooze | not_today | done | undo
);
CREATE INDEX IF NOT EXISTS interaction_day ON interaction(day, action);

CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "parent_pin": "",          # blank = settings window opens without a PIN
    "idle_limit_s": "60",      # only nag if he touched the keyboard this recently
    "warmup_s": "1200",        # 20 min of session before the first prompt of the day
    "monitor": "pointer",      # pointer | primary
}


def default_db_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "nag" / "nag.db"


class Store:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self._migrate()
        for k, v in DEFAULT_SETTINGS.items():
            self.db.execute("INSERT OR IGNORE INTO setting(key, value) VALUES (?, ?)", (k, v))

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(task)")}
        if "days" not in have:
            self.db.execute("ALTER TABLE task ADD COLUMN days TEXT NOT NULL DEFAULT ''")

    # -- settings ---------------------------------------------------------- #

    def get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, str(default)))
        except ValueError:
            return default

    def set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO setting(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    # -- tasks ------------------------------------------------------------- #

    def tasks(self, active_only: bool = True) -> list[Task]:
        sql = "SELECT * FROM task"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY name"
        return [
            Task(
                id=r["id"],
                name=r["name"],
                per_week=r["per_week"],
                max_per_day=r["max_per_day"],
                active=bool(r["active"]),
                days=parse_days(r["days"]),
            )
            for r in self.db.execute(sql)
        ]

    def add_task(
        self,
        name: str,
        per_week: int,
        max_per_day: int = 1,
        days: frozenset[int] | str | None = None,
    ) -> int:
        spec = days if isinstance(days, str) else format_days(days)
        spec = "" if spec in ("", "any day") else spec
        # Build the Task first: its __post_init__ rejects an impossible quota
        # (e.g. 3x a week on Sundays only) before anything is written.
        Task(0, name.strip(), int(per_week), int(max_per_day), True, parse_days(spec))
        cur = self.db.execute(
            "INSERT INTO task(name, per_week, max_per_day, days) VALUES (?, ?, ?, ?)",
            (name.strip(), int(per_week), int(max_per_day), spec),
        )
        return int(cur.lastrowid)

    def set_days(self, task_id: int, days: frozenset[int] | None) -> None:
        spec = format_days(days)
        self.db.execute(
            "UPDATE task SET days=? WHERE id=?",
            ("" if spec == "any day" else spec, task_id),
        )

    def update_task(self, task_id: int, **fields) -> None:
        allowed = {"name", "per_week", "max_per_day", "active", "days"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        clause = ", ".join(f"{k}=?" for k in sets)
        self.db.execute(f"UPDATE task SET {clause} WHERE id=?", (*sets.values(), task_id))

    def retire_task(self, task_id: int) -> None:
        """Deactivate rather than delete -- history stays readable."""
        self.db.execute("UPDATE task SET active=0 WHERE id=?", (task_id,))

    # -- completions ------------------------------------------------------- #

    def credited_days(self, task: Task, now: dt.datetime) -> list[dt.date]:
        ws = week_start_date(now).isoformat()
        rows = self.db.execute(
            "SELECT credited_date FROM completion "
            "WHERE task_id=? AND week_start=? AND undone=0",
            (task.id, ws),
        )
        return [dt.date.fromisoformat(r["credited_date"]) for r in rows]

    def mark_done(self, task: Task, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now()
        cur = self.db.execute(
            "INSERT INTO completion"
            "(task_id, done_at, credited_date, week_start, target_at_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                task.id,
                now.isoformat(timespec="seconds"),
                credited_date(now).isoformat(),
                week_start_date(now).isoformat(),
                task.per_week,
            ),
        )
        self.log(task.id, "done", now)
        return int(cur.lastrowid)

    def undo(self, completion_id: int, now: dt.datetime | None = None) -> None:
        self.db.execute("UPDATE completion SET undone=1 WHERE id=?", (completion_id,))
        self.log(None, "undo", now)

    # -- interaction log --------------------------------------------------- #

    def log(self, task_id: int | None, action: str, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now()
        self.db.execute(
            "INSERT INTO interaction(task_id, at, day, action) VALUES (?, ?, ?, ?)",
            (task_id, now.isoformat(timespec="seconds"), credited_date(now).isoformat(), action),
        )

    def count_today(self, action: str, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now()
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM interaction WHERE day=? AND action=?",
            (credited_date(now).isoformat(), action),
        ).fetchone()
        return int(row["n"])

    def snoozes_today(self, task_id: int, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now()
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM interaction "
            "WHERE day=? AND action='snooze' AND (task_id=? OR task_id IS NULL)",
            (credited_date(now).isoformat(), task_id),
        ).fetchone()
        return int(row["n"])

    def skipped_today(self, task_id: int, now: dt.datetime | None = None) -> bool:
        now = now or dt.datetime.now()
        row = self.db.execute(
            "SELECT 1 FROM interaction WHERE day=? AND action='not_today' AND task_id=? LIMIT 1",
            (credited_date(now).isoformat(), task_id),
        ).fetchone()
        return row is not None

    def shown_today(self, task_id: int, now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now()
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM interaction "
            "WHERE day=? AND action='shown' AND task_id=?",
            (credited_date(now).isoformat(), task_id),
        ).fetchone()
        return int(row["n"])

    # -- reporting --------------------------------------------------------- #

    def week_report(self, now: dt.datetime | None = None) -> list[dict]:
        now = now or dt.datetime.now()
        out = []
        for t in self.tasks():
            out.append({"task": t.name, "done": len(self.credited_days(t, now)), "target": t.per_week})
        return out

    def past_weeks(self, limit: int = 8) -> list[dict]:
        rows = self.db.execute(
            "SELECT week_start, task_id, COUNT(*) AS done, MAX(target_at_time) AS target "
            "FROM completion WHERE undone=0 "
            "GROUP BY week_start, task_id ORDER BY week_start DESC LIMIT ?",
            (limit * 8,),
        ).fetchall()
        names = {t.id: t.name for t in self.tasks(active_only=False)}
        return [
            {
                "week_start": r["week_start"],
                "task": names.get(r["task_id"], f"#{r['task_id']}"),
                "done": r["done"],
                "target": r["target"],
            }
            for r in rows
        ]

    def last_shown_at(self, task_id: int) -> dt.datetime | None:
        row = self.db.execute(
            "SELECT at FROM interaction WHERE task_id=? AND action='shown' "
            "ORDER BY at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return dt.datetime.fromisoformat(row["at"]) if row else None
