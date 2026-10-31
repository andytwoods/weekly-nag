"""The reminder card: one GTK3 window, all pending tasks, never steals focus.

Deliberate choices, each one a thing all three consulted models warned about:

* **A real window, not ``notify-send``.** Cinnamon's notification daemon times
  out and Do-Not-Disturb silently swallows notifications.
* **``set_accept_focus(False)`` and ``set_focus_on_map(False)``.** The card
  floats over a game or an essay without eating a single keystroke. Stealing
  focus mid-sentence is the fastest route to the process being killed.
* **Hold to mark Done; one click to snooze.** Lying is physically slower than
  deferring, so a reflex click can never record a task that did not happen.
* **No sound.** Audio during a voice call gets the app uninstalled.
* **Never full-screen.** The one escalation is moving to the centre of the
  screen once a task is must-do-today.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .core import TaskState, Urgency, snooze_lock_seconds  # noqa: E402

HOLD_MS = 1500
TICK_MS = 40
MARGIN = 24

CSS = b"""
/* The window itself must be transparent, otherwise the theme's (light)
   background shows as a square frame around the rounded card. */
window.nag-window { background-color: transparent; }
.nag-card      { background: #1e2228; border-radius: 14px; border: 1px solid #333a44;
                 padding: 18px; }
.nag-title     { color: #f2f4f7; font-size: 15px; font-weight: 700; }
.nag-sub       { color: #9aa4b2; font-size: 11px; }
.nag-task      { color: #e8ebf0; font-size: 13px; font-weight: 600; }
.nag-pips      { color: #7dd3a0; font-size: 13px; }
.nag-must      { color: #ffb4a2; font-size: 11px; font-weight: 700; }
.nag-hold      { background: #2f6f4f; color: #ffffff; border: none; border-radius: 9px;
                 padding: 7px 14px; font-size: 12px; font-weight: 700; }
.nag-hold:hover{ background: #38835d; }
.nag-snooze    { background: #272c34; color: #c8cfda; border: 1px solid #3a4250;
                 border-radius: 9px; padding: 5px 11px; font-size: 11px; }
.nag-snooze:disabled { color: #5b636f; }
.nag-row       { background: #23272e; border-radius: 10px; padding: 9px; }
progressbar.nag-fill trough { min-height: 3px; background: transparent; border: none; }
progressbar.nag-fill progress { min-height: 3px; background: #a7f3c8; }
"""


def _transparent(window: Gtk.Window) -> None:
    """Give the window an RGBA visual so only the rounded card is drawn.

    Falls back silently to an opaque window if the screen is not composited;
    the card is still readable, just with square corners.
    """
    window.get_style_context().add_class("nag-window")
    window.set_app_paintable(True)
    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if visual is not None and screen.is_composited():
        window.set_visual(visual)


def _install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


class HoldButton(Gtk.Button):
    """A button that only fires after the pointer is held down for ``HOLD_MS``."""

    def __init__(self, label: str, on_complete) -> None:
        super().__init__()
        self._on_complete = on_complete
        self._timer: int | None = None
        self._elapsed = 0

        self._label = Gtk.Label(label=label)
        self._fill = Gtk.ProgressBar()
        self._fill.get_style_context().add_class("nag-fill")
        self._fill.set_valign(Gtk.Align.END)
        self._fill.set_fraction(0.0)

        overlay = Gtk.Overlay()
        overlay.add(self._label)
        overlay.add_overlay(self._fill)
        self.add(overlay)
        self.get_style_context().add_class("nag-hold")

        self.connect("button-press-event", self._press)
        self.connect("button-release-event", self._release)
        self.connect("leave-notify-event", self._release)

    def _press(self, *_a) -> bool:
        self._cancel()
        self._elapsed = 0
        self._timer = GLib.timeout_add(TICK_MS, self._tick)
        return True

    def _tick(self) -> bool:
        self._elapsed += TICK_MS
        self._fill.set_fraction(min(self._elapsed / HOLD_MS, 1.0))
        if self._elapsed >= HOLD_MS:
            self._timer = None
            self._fill.set_fraction(0.0)
            self._on_complete()
            return False
        return True

    def _release(self, *_a) -> bool:
        self._cancel()
        self._fill.set_fraction(0.0)
        return True

    def _cancel(self) -> None:
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None


class ReminderCard(Gtk.Window):
    """One card listing every pending task. Bundled, never one window per task."""

    def __init__(
        self,
        states: list[TaskState],
        snoozes_today: int,
        on_done,
        on_snooze,
        on_not_today,
        on_dismiss,
        monitor_pref: str = "pointer",
    ) -> None:
        super().__init__(title="Reminders")
        _install_css()

        self._states = states
        self._on_done = on_done
        self._on_snooze = on_snooze
        self._on_not_today = on_not_today
        self._on_dismiss = on_dismiss
        self._lock_timer: int | None = None

        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_keep_above(True)
        self.set_accept_focus(False)      # never eat a keystroke
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.stick()                      # visible on every workspace
        self.set_default_size(440, -1)
        _transparent(self)
        self.connect("delete-event", self._closed)

        self._build(states, snoozes_today)
        self._place(any(s.centre_screen for s in states), monitor_pref)

    # -- layout ------------------------------------------------------------ #

    def _build(self, states: list[TaskState], snoozes_today: int) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_margin_top(6)
        outer.set_margin_bottom(6)
        outer.set_margin_start(6)
        outer.set_margin_end(6)
        outer.get_style_context().add_class("nag-card")

        must = [s for s in states if s.centre_screen]
        title = Gtk.Label(xalign=0.0)
        title.set_markup("<b>Still to do this week</b>")
        title.get_style_context().add_class("nag-title")
        outer.pack_start(title, False, False, 0)

        if must:
            warn = Gtk.Label(xalign=0.0)
            warn.set_text(
                "Last chance today - skip it and this week's target is gone."
                if len(must) == 1
                else f"{len(must)} of these have to happen today."
            )
            warn.get_style_context().add_class("nag-must")
            warn.set_line_wrap(True)
            outer.pack_start(warn, False, False, 0)

        missed = [s for s in states if s.urgency is Urgency.MISSED]
        if missed and not must:
            note = Gtk.Label(xalign=0.0)
            note.set_text(
                "This week's number is out of reach now - still worth doing one."
            )
            note.get_style_context().add_class("nag-sub")
            note.set_line_wrap(True)
            outer.pack_start(note, False, False, 0)

        for st in states:
            outer.pack_start(self._row(st), False, False, 0)

        self._snooze_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._snooze_box.set_halign(Gtk.Align.END)
        for label, minutes in (("+15 min", 15), ("+1 hour", 60)):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("nag-snooze")
            b.connect("clicked", lambda _b, m=minutes: self._snooze(m))
            self._snooze_box.pack_start(b, False, False, 0)

        if all(s.allow_not_today for s in states):
            b = Gtk.Button(label="Not today")
            b.get_style_context().add_class("nag-snooze")
            b.connect("clicked", self._not_today)
            self._snooze_box.pack_start(b, False, False, 0)

        outer.pack_start(self._snooze_box, False, False, 0)

        hint = Gtk.Label(xalign=1.0)
        hint.set_text("Closing this only snoozes it.")
        hint.set_line_wrap(True)
        hint.set_max_width_chars(40)
        hint.get_style_context().add_class("nag-sub")
        outer.pack_start(hint, False, False, 0)

        self.add(outer)

        lock = max(
            (snooze_lock_seconds(s.urgency, snoozes_today) for s in states), default=0
        )
        if lock:
            self._snooze_box.set_sensitive(False)
            self._lock_timer = GLib.timeout_add_seconds(lock, self._unlock)

    def _row(self, st: TaskState) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.get_style_context().add_class("nag-row")

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=st.task.name, xalign=0.0)
        name.get_style_context().add_class("nag-task")
        pips = Gtk.Label(label=f"{st.pips()}   {st.remaining} left", xalign=0.0)
        pips.get_style_context().add_class("nag-pips")
        left.pack_start(name, False, False, 0)
        left.pack_start(pips, False, False, 0)

        btn = HoldButton("Hold to mark done", lambda s=st: self._done(s))
        btn.set_valign(Gtk.Align.CENTER)

        row.pack_start(left, True, True, 0)
        row.pack_start(btn, False, False, 0)
        return row

    def _place(self, centre: bool, monitor_pref: str) -> None:
        display = Gdk.Display.get_default()
        if monitor_pref == "pointer":
            seat = display.get_default_seat()
            _s, x, y = seat.get_pointer().get_position()
            monitor = display.get_monitor_at_point(x, y) or display.get_primary_monitor()
        else:
            monitor = display.get_primary_monitor()
        area = monitor.get_workarea()

        self.show_all()
        w, h = self.get_size()
        if centre:
            self.move(area.x + (area.width - w) // 2, area.y + (area.height - h) // 3)
        else:
            self.move(area.x + area.width - w - MARGIN, area.y + MARGIN)

    # -- actions ----------------------------------------------------------- #

    def _unlock(self) -> bool:
        self._snooze_box.set_sensitive(True)
        self._lock_timer = None
        return False

    def _teardown(self) -> None:
        if self._lock_timer is not None:
            GLib.source_remove(self._lock_timer)
            self._lock_timer = None
        self.destroy()

    def _done(self, st: TaskState) -> None:
        self._teardown()
        self._on_done(st)

    def _snooze(self, minutes: int) -> None:
        self._teardown()
        self._on_snooze(minutes)

    def _not_today(self, *_a) -> None:
        self._teardown()
        self._on_not_today(self._states)

    def _closed(self, *_a) -> bool:
        """The window-manager X is a snooze, never a completion."""
        self._teardown()
        self._on_dismiss()
        return True


class UndoToast(Gtk.Window):
    """A small 'Marked done - Undo' strip, so a mis-hold costs nothing."""

    def __init__(self, text: str, on_undo, seconds: int = 6) -> None:
        super().__init__(title="Done")
        _install_css()
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_decorated(False)
        self.stick()
        _transparent(self)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.get_style_context().add_class("nag-card")
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("nag-task")
        btn = Gtk.Button(label="Undo")
        btn.get_style_context().add_class("nag-snooze")
        btn.connect("clicked", lambda *_a: (self.destroy(), on_undo()))
        box.pack_start(label, True, True, 0)
        box.pack_start(btn, False, False, 0)
        self.add(box)

        monitor = Gdk.Display.get_default().get_primary_monitor()
        area = monitor.get_workarea()
        self.show_all()
        w, h = self.get_size()
        self.move(area.x + area.width - w - MARGIN, area.y + area.height - h - MARGIN)
        GLib.timeout_add_seconds(seconds, self._expire)

    def _expire(self) -> bool:
        if self.get_realized():
            self.destroy()
        return False
