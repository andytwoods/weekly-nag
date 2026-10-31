"""Is he actually at the keyboard?

Gemini's point, and the one the other two missed: a reminder fired into an empty
room or over a locked screen is a reminder spent for nothing, and it trains the
habit of finding the window already there and clicking it away unread.

``xprintidle`` (``apt install xprintidle``) gives X11 idle time in ms. If it is
missing we fail *open* -- better to nag than to go silent for ever.
"""

from __future__ import annotations

import shutil
import subprocess

_XPRINTIDLE = shutil.which("xprintidle")


def idle_seconds() -> float | None:
    """Seconds since the last key press or mouse move, or ``None`` if unknown."""
    if not _XPRINTIDLE:
        return None
    try:
        out = subprocess.run(
            [_XPRINTIDLE], capture_output=True, text=True, timeout=3, check=True
        ).stdout.strip()
        return int(out) / 1000.0
    except (subprocess.SubprocessError, ValueError):
        return None


def screen_locked() -> bool:
    """True if the Cinnamon screensaver is up. Unknown counts as unlocked."""
    try:
        out = subprocess.run(
            ["cinnamon-screensaver-command", "-q"],
            capture_output=True, text=True, timeout=3,
        ).stdout.lower()
        return "is active" in out
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def at_keyboard(limit_s: int) -> bool:
    if screen_locked():
        return False
    idle = idle_seconds()
    return True if idle is None else idle <= limit_s
