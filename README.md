# nag

**Reminders for things that need doing *N times a week*, on a Linux Mint desktop.**

Most reminder apps want a time: "9am on Tuesday". Some things aren't like that.
Physio four times a week. Guitar practice twice. Feed the rabbit daily. *When*
doesn't matter; *how many times by Sunday* does.

Some things are in between — "take the bins out once a week, but it has to be
Saturday or Sunday" — so a task can be restricted to certain weekdays and still
be counted as a quota.

`nag` keeps the count, works out how much slack is left, and puts a card on the
screen that is **insistent but dismissible** — it comes back until the task is
actually marked done, without ever stealing focus, going full-screen, or making
a sound.

Built for a family desktop, where the person who needs reminding and the person
who sets the targets are two different people.

```
┌──────────────────────────────────────────────┐
│  Still to do this week                       │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │ Physio                                 │  │
│  │ ● ● ○ ○   2 left     [Hold to mark done]│ │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │ Guitar                                 │  │
│  │ ● ○       1 left     [Hold to mark done]│ │
│  └────────────────────────────────────────┘  │
│                                              │
│            [+15 min] [+1 hour] [Not today]   │
│                 Closing this only snoozes it.│
└──────────────────────────────────────────────┘
```

## Install

Linux Mint 21 or 22 (Cinnamon, X11). Nothing comes from pip.

```bash
git clone https://github.com/andytwoods/weekly-nag ~/nag
cd ~/nag && ./install.sh
```

The installer pulls `python3-gi`, `gir1.2-gtk-3.0` and `xprintidle` from apt,
puts a launcher in `~/.local/bin/nag`, starts the daemon at login via
`~/.config/autostart`, adds a settings entry to the menu, and enables a
five-minute watchdog timer that restarts the daemon if it stops.

## Use

```bash
nag add Physio 4                             # four times a week, any day
nag add Guitar 2
nag add "Rubbish" 1 --days sat,sun           # once a week, weekends only
nag status                                   # this week at a glance
nag done physio             # credit one from the command line
nag history                 # past weeks
nag settings                # GUI: tasks / this week / options
nag test                    # show the card right now, recording nothing
nag test --must             # ...the must-do-today version, centre screen
```

```
$ nag status
Week starting 2026-11-02 (resets Mon 04:00)
  Guitar          ● ○              1/2  1 left over 5d                   [slack]
  Physio          ● ● ○ ○          2/4  2 left over 5d                   [pace]
  Rubbish         ○                0/1  1 left, next chance sat,sun      [waiting]  on sat,sun
```

`status`, `add`, `done` and `history` need no GTK, so they work fine over SSH.

The daemon deliberately stays quiet for the first 20 minutes after login, so
`nag test` is the way to check the card actually appears and behaves — it shows
one immediately and records nothing.

## How the schedule works

Each task is just a name and a number: *do this N times a week*. From that and
the day of the week, `nag` works out how hard to push.

| State | Meaning | Behaviour |
|---|---|---|
| `slack` | comfortably ahead | one gentle prompt a day |
| `pace` | needs most of the remaining days | returns every 90 min, at most 4 times |
| `must` | the target dies unless it happens today | every 30 min, then 15 after three brush-offs; card moves to the centre of the screen; "Not today" withdrawn |
| `missed` | the target is already out of reach | backs off to one honest prompt a day |
| `waiting` | not one of this task's days | silent |
| `today_done` / `done` | credited today / target met | silent |

- The week runs **Monday 04:00 → Monday 04:00**. A 01:30 completion counts for
  the previous day, because people stay up past midnight.
- **Nothing rolls over.** Monday wipes the slate and the old week goes to
  history. Starting a week owing seven sessions is how an app gets uninstalled.
- **One credit per task per day** by default, so four physio sessions can't be
  crammed into Sunday evening. Raise `max/day` per task for things genuinely
  done more than once a day.
- A task can be **restricted to certain weekdays** (`--days sat,sun`, or the
  `M T W T F S S` toggles in the GUI). Urgency is then measured against the
  *allowed* days left, not the calendar days left: a weekends-only task has two
  chances left on Monday, so it stays silent all week, nudges once on Saturday,
  and insists on Sunday. A target that cannot fit its days is refused up front.
- Frequency ramps with urgency. **Loudness never does.** The single escalation
  is moving to the centre of the screen.

## Why the pop-up behaves the way it does

Nagging software fails in a specific way: the target learns to dismiss it
without reading it. Nearly every decision here is aimed at that.

- **A real GTK window, not `notify-send`.** Cinnamon's notification daemon
  times out, and Do-Not-Disturb silently swallows notifications. A reminder you
  can't rely on isn't a reminder.
- **Never takes focus** (`set_accept_focus(False)`, `set_focus_on_map(False)`).
  The card floats over a game or an essay without eating one keystroke.
  Interrupting someone mid-sentence is the fastest route to `pkill`.
- **Hold 1.5 s to mark done; one click to snooze.** Lying is deliberately
  *slower* than deferring, so a reflex click can never record something that
  didn't happen.
- **Closing the window is a snooze, never a completion** — and the card says so.
- **Undo** for six seconds after every completion, so a mis-hold costs nothing.
- **One bundled card**, never one window per task.
- **Silent.** Audio during a voice call gets the app uninstalled.
- **Never full-screen, never modal.** That reads as malware.
- **Hard cap of 8 reminder rounds a day** across all tasks. This is the single
  most important setting in the app.
- **Only prompts when someone is actually there** (`xprintidle` under 60 s,
  screen unlocked), so reminders aren't spent on an empty room.
- **Silent on days a task isn't allowed on.** Nagging about something that
  can't usefully be done yet is how an app loses credibility.
- **Weekly pips, no daily streak.** A streak is one bad day from being dead for
  good, which destroys the motivation it was meant to build.
- **No surveillance, no lockouts.** Completion is self-reported. If you doubt
  it, check the real-world outcome — policing it in software turns the app into
  the thing you argue about. (Blocking games until tasks were done was
  considered and deliberately dropped.)

The design was cross-reviewed against GPT, Gemini and Qwen3.5-397B. They agreed
on the flexible weekly quota, the no-rollover rule, owning a real window instead
of using notifications, and never stealing focus. They disagreed about
escalation — one argued escalation itself teaches dismissal, another wanted a
centre-screen stage — which is why frequency ramps and intensity doesn't.

## Layout

| File | Role |
|---|---|
| `nag/core.py` | all scheduling logic — pure, clock injected, no I/O |
| `nag/store.py` | SQLite: tasks, settings, append-only completion + interaction log |
| `nag/daemon.py` | the resident loop: decide, show, record |
| `nag/popup.py` | the reminder card and the undo toast |
| `nag/settings_ui.py` | GTK settings window (tasks / this week / options) |
| `nag/idle.py` | is anyone at the keyboard? |
| `nag/__main__.py` | CLI |

Data lives in `~/.local/share/nag/nag.db`. Completions are stored as **events**,
not a counter: undo and corrections leave history intact, and every row freezes
the target as it stood at the time, so editing a task later can't rewrite a past
week's result.

Started from `~/.config/autostart` rather than a `systemd --user` service,
because user services race the X11 session and come up without `DISPLAY` or
`DBUS_SESSION_BUS_ADDRESS`. A systemd *timer* is used only as the watchdog.

## Tests

```bash
uv run pytest        # 46 tests
```

All scheduling logic is pure and takes the clock as an argument, so the suite
runs anywhere — including macOS, with no GTK installed.

## Known limits

- It can only remind someone **while this computer is in use**. A phone would
  cover the rest; out of scope.
- This is a *weekly quota* model with optional weekday restrictions. It has no
  concept of a time of day. **Don't use it for prescription medication** — that
  needs fixed times and proper missed-dose handling, not catch-up arithmetic.
- X11 only. Wayland has no `xprintidle` equivalent and different window hints.
- The watchdog restarts the daemon; it doesn't stop a determined person
  disabling it. That's deliberate — an arms race is one you lose.

## Licence

MIT. See [LICENSE](LICENSE).
