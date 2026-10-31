#!/usr/bin/env bash
# Install the reminder app for the *current user* on Linux Mint (Cinnamon, X11).
# Idempotent: safe to re-run after a git pull.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
AUTOSTART_DIR="$HOME/.config/autostart"
DESKTOP_DIR="$HOME/.local/share/applications"
UNIT_DIR="$HOME/.config/systemd/user"

echo "== 1/5 apt dependencies =="
# python3-gi + the GTK3 typelib are what PyGObject needs; pip install PyGObject
# would try to compile against headers that are not there. xprintidle gives the
# idle check that stops pop-ups firing into an empty room.
sudo apt-get install -y python3 python3-gi gir1.2-gtk-3.0 xprintidle

echo "== 2/5 launcher =="
mkdir -p "$BIN_DIR"
# PYTHONPATH in the wrapper, so nothing is installed into site-packages and a
# `git pull` in $APP_DIR is the whole upgrade path.
cat > "$BIN_DIR/nag" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m nag "\$@"
WRAP
chmod +x "$BIN_DIR/nag"

echo "== 3/5 autostart at login =="
# ~/.config/autostart, NOT a systemd --user service: user services race the X11
# session and come up with no DISPLAY / DBUS_SESSION_BUS_ADDRESS. The delay lets
# Cinnamon finish drawing the panel first.
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/nag.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Reminders
Comment=Weekly task reminders
Exec=$BIN_DIR/nag daemon
Icon=appointment-soon
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=10
DESK

echo "== 4/5 settings menu entry =="
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/nag-settings.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Reminders - settings
Comment=Edit the weekly task list
Exec=$BIN_DIR/nag settings
Icon=preferences-system-time
Terminal=false
Categories=Utility;
DESK
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "== 5/5 watchdog timer =="
# If the daemon is killed, bring it back within 5 minutes. A gentle restart, not
# tamper-proofing -- an arms race with a teenager is a race you lose.
mkdir -p "$UNIT_DIR"
cat > "$BIN_DIR/nag-watchdog" <<'WATCH'
#!/usr/bin/env bash
# Restart the reminder daemon if it is not running and somebody is logged in to X.
pgrep -u "$(id -u)" -f "python3 -m nag daemon" >/dev/null && exit 0
# Inherit DISPLAY/DBUS from the live Cinnamon session rather than guessing :0.
cin=$(pgrep -u "$(id -u)" -x cinnamon | head -1) || exit 0
[ -n "$cin" ] || exit 0
env_of() { tr '\0' '\n' < "/proc/$cin/environ" | grep -m1 "^$1=" || true; }
export "$(env_of DISPLAY)" 2>/dev/null || exit 0
export "$(env_of DBUS_SESSION_BUS_ADDRESS)" 2>/dev/null || true
export "$(env_of XAUTHORITY)" 2>/dev/null || true
setsid "$HOME/.local/bin/nag" daemon >/dev/null 2>&1 &
WATCH
chmod +x "$BIN_DIR/nag-watchdog"

cat > "$UNIT_DIR/nag-watchdog.service" <<UNIT
[Unit]
Description=Restart the reminder daemon if it stopped

[Service]
Type=oneshot
ExecStart=$BIN_DIR/nag-watchdog
UNIT

cat > "$UNIT_DIR/nag-watchdog.timer" <<UNIT
[Unit]
Description=Check the reminder daemon every 5 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now nag-watchdog.timer

echo
echo "Installed. Next:"
echo "  nag add Physio 4          # add tasks"
echo "  nag status                # see this week"
echo "  nag settings              # parent GUI (also in the menu)"
echo "  nag daemon &              # start now without logging out"
