"""Parent settings window: edit the task list, see this week, nothing else.

Opened with ``nag settings``. Optionally behind a PIN (blank by default) -- a
speed bump so the quotas are not edited casually, not a security boundary.
"""

from __future__ import annotations

import datetime as dt

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from .core import DAY_NAMES, Urgency, evaluate, format_days  # noqa: E402
from .store import Store  # noqa: E402


def _ask_pin(expected: str) -> bool:
    if not expected:
        return True
    dialog = Gtk.Dialog(title="Parent PIN")
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
    entry = Gtk.Entry(visibility=False, activates_default=True)
    dialog.set_default_response(Gtk.ResponseType.OK)
    box = dialog.get_content_area()
    box.set_border_width(14)
    box.set_spacing(8)
    box.add(Gtk.Label(label="PIN:"))
    box.add(entry)
    dialog.show_all()
    ok = dialog.run() == Gtk.ResponseType.OK and entry.get_text() == expected
    dialog.destroy()
    return ok


class DayPicker(Gtk.Box):
    """Seven toggles: M T W T F S S. None lit means "any day"."""

    LETTERS = ("M", "T", "W", "T", "F", "S", "S")

    def __init__(self, days: frozenset[int] | None, on_change) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._on_change = on_change
        self._quiet = False
        self._buttons = []
        for i, letter in enumerate(self.LETTERS):
            b = Gtk.ToggleButton(label=letter)
            b.set_tooltip_text(DAY_NAMES[i].capitalize())
            b.set_active(bool(days) and i in days)
            b.connect("toggled", self._changed)
            self._buttons.append(b)
            self.pack_start(b, False, False, 0)

    def selected(self) -> frozenset[int] | None:
        chosen = frozenset(i for i, b in enumerate(self._buttons) if b.get_active())
        return chosen or None          # nothing lit == no restriction

    def _changed(self, *_a) -> None:
        if not self._quiet:
            self._on_change(self.selected())


class SettingsWindow(Gtk.Window):
    def __init__(self, store: Store) -> None:
        super().__init__(title="Reminders - settings")
        self.store = store
        self.set_default_size(560, 520)
        self.set_border_width(14)
        self.connect("destroy", Gtk.main_quit)

        notebook = Gtk.Notebook()
        notebook.append_page(self._tasks_page(), Gtk.Label(label="Tasks"))
        notebook.append_page(self._week_page(), Gtk.Label(label="This week"))
        notebook.append_page(self._options_page(), Gtk.Label(label="Options"))
        self.add(notebook)

    # -- tasks ------------------------------------------------------------- #

    def _tasks_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_border_width(12)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self._list)
        page.pack_start(scroll, True, True, 0)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._new_name = Gtk.Entry()
        self._new_name.set_placeholder_text("New task, e.g. Physio")
        self._new_per_week = Gtk.SpinButton.new_with_range(1, 14, 1)
        self._new_per_week.set_value(3)
        add_btn = Gtk.Button(label="Add")
        add_btn.connect("clicked", self._add)
        add_row.pack_start(self._new_name, True, True, 0)
        add_row.pack_start(Gtk.Label(label="per week"), False, False, 0)
        add_row.pack_start(self._new_per_week, False, False, 0)
        add_row.pack_start(add_btn, False, False, 0)
        page.pack_start(add_row, False, False, 0)

        hint = Gtk.Label(xalign=0.0)
        hint.set_line_wrap(True)
        hint.set_text(
            "M T W T F S S restricts a task to certain days - leave all off for "
            "any day. Useful for things with a real deadline, like bin night."
        )
        page.pack_start(hint, False, False, 0)

        self._reload()
        return page

    def _reload(self) -> None:
        for child in self._list.get_children():
            self._list.remove(child)
        for task in self.store.tasks(active_only=False):
            self._list.add(self._task_row(task))
        self._list.show_all()

    def _task_row(self, task) -> Gtk.Widget:
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_border_width(8)

        name = Gtk.Entry(text=task.name)
        name.connect("changed", lambda w, t=task: self.store.update_task(t.id, name=w.get_text()))

        per_week = Gtk.SpinButton.new_with_range(1, 14, 1)
        per_week.set_value(task.per_week)
        per_week.connect(
            "value-changed",
            lambda w, t=task: self.store.update_task(t.id, per_week=int(w.get_value())),
        )

        per_day = Gtk.SpinButton.new_with_range(1, 4, 1)
        per_day.set_value(task.max_per_day)
        per_day.connect(
            "value-changed",
            lambda w, t=task: self.store.update_task(t.id, max_per_day=int(w.get_value())),
        )

        days = DayPicker(task.days, lambda sel, t=task: self._set_days(t, sel))
        days.set_valign(Gtk.Align.CENTER)

        active = Gtk.Switch(active=task.active)
        active.set_valign(Gtk.Align.CENTER)
        active.connect(
            "notify::active",
            lambda w, _p, t=task: self.store.update_task(t.id, active=int(w.get_active())),
        )

        box.pack_start(name, True, True, 0)
        box.pack_start(Gtk.Label(label="x/week"), False, False, 0)
        box.pack_start(per_week, False, False, 0)
        box.pack_start(Gtk.Label(label="max/day"), False, False, 0)
        box.pack_start(per_day, False, False, 0)
        box.pack_start(days, False, False, 0)
        box.pack_start(active, False, False, 0)
        row.add(box)
        return row

    def _set_days(self, task, selected) -> None:
        """Reject a day change that makes the weekly target unreachable."""
        slots = len(selected) if selected else 7
        if task.per_week > slots * task.max_per_day:
            self._warn(
                f"{task.name} is {task.per_week}x a week, which will not fit on "
                f"{format_days(selected)}. Lower the weekly number first."
            )
            self._reload()
            return
        self.store.set_days(task.id, selected)

    def _warn(self, text: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text=text,
        )
        dialog.run()
        dialog.destroy()

    def _add(self, *_a) -> None:
        name = self._new_name.get_text().strip()
        if not name:
            return
        self.store.add_task(name, int(self._new_per_week.get_value()))
        self._new_name.set_text("")
        self._reload()

    # -- this week --------------------------------------------------------- #

    def _week_page(self) -> Gtk.Widget:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_border_width(12)
        now = dt.datetime.now()
        for task in self.store.tasks():
            st = evaluate(task, self.store.credited_days(task, now), now)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            label = task.name if task.days is None else f"{task.name}  ({task.days_label})"
            row.pack_start(Gtk.Label(label=label, xalign=0.0), True, True, 0)
            row.pack_start(Gtk.Label(label=st.pips()), False, False, 0)
            state = "waiting for its day" if st.urgency is Urgency.WAITING else st.urgency.value
            row.pack_start(
                Gtk.Label(label=f"{st.done_this_week}/{task.per_week}  ({state})"),
                False, False, 0,
            )
            page.pack_start(row, False, False, 0)
        if not self.store.tasks():
            page.pack_start(Gtk.Label(label="No tasks yet - add one on the Tasks tab."), False, False, 0)
        return page

    # -- options ----------------------------------------------------------- #

    def _options_page(self) -> Gtk.Widget:
        grid = Gtk.Grid(row_spacing=10, column_spacing=10)
        grid.set_border_width(12)

        def spin(key: str, lo: int, hi: int, step: int):
            w = Gtk.SpinButton.new_with_range(lo, hi, step)
            w.set_value(self.store.get_int(key))
            w.connect("value-changed", lambda b, k=key: self.store.set(k, int(b.get_value())))
            return w

        grid.attach(Gtk.Label(label="Only nag if active in the last (seconds)", xalign=0.0), 0, 0, 1, 1)
        grid.attach(spin("idle_limit_s", 15, 600, 15), 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Quiet period after login (seconds)", xalign=0.0), 0, 1, 1, 1)
        grid.attach(spin("warmup_s", 0, 7200, 300), 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Show card on", xalign=0.0), 0, 2, 1, 1)
        combo = Gtk.ComboBoxText()
        for key, label in (("pointer", "monitor with the mouse"), ("primary", "primary monitor")):
            combo.append(key, label)
        combo.set_active_id(self.store.get("monitor", "pointer"))
        combo.connect("changed", lambda c: self.store.set("monitor", c.get_active_id() or "pointer"))
        grid.attach(combo, 1, 2, 1, 1)

        grid.attach(Gtk.Label(label="Parent PIN (blank = none)", xalign=0.0), 0, 3, 1, 1)
        pin = Gtk.Entry(text=self.store.get("parent_pin", ""))
        pin.connect("changed", lambda w: self.store.set("parent_pin", w.get_text()))
        grid.attach(pin, 1, 3, 1, 1)

        note = Gtk.Label(xalign=0.0)
        note.set_line_wrap(True)
        note.set_text(
            "Quotas reset every Monday at 04:00 and nothing carries over. "
            "Changes take effect within 30 seconds; no restart needed."
        )
        grid.attach(note, 0, 4, 2, 1)
        return grid


def main() -> int:
    store = Store()
    if not _ask_pin(store.get("parent_pin", "")):
        return 1
    win = SettingsWindow(store)
    win.show_all()
    Gtk.main()
    return 0
