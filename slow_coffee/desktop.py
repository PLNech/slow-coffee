"""Self-integration: drop a .desktop entry + icon into the user's XDG dirs.

This is what makes ``slow-coffee`` appear in "installed applications" even from a
``pip install --user`` - no root, no distro package required. Distro packages
(deb/AUR) install the same files system-wide via their own rules instead.
"""

import os
import shutil
from os.path import join, dirname, abspath, exists

from . import APP_ID


def _desktop_body(exec_cmd="slow-coffee", autostart=False):
    """Build a .desktop entry. ``exec_cmd`` lets the autostart copy encode
    "coffee at boot" (``slow-coffee`` vs ``slow-coffee --start-inactive``)."""
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Slow Coffee",
        "GenericName=Keep Awake",
        "Comment=Keep your screen awake - on by default, sip at your own pace",
        "Exec=" + exec_cmd,
        "Icon=slow-coffee",
        "Terminal=false",
        "Categories=Utility;GTK;",
        "Keywords=caffeine;caffeinate;awake;idle;screensaver;inhibit;presentation;",
        "StartupNotify=false",
    ]
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines) + "\n"


def _autostart_exec(start_active):
    return "slow-coffee" if start_active else "slow-coffee --start-inactive"


def _xdg(*parts):
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return join(base, *parts)


def _config(*parts):
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return join(base, *parts)


def _src_icon():
    return join(dirname(abspath(__file__)), "icons", "slow-coffee-full.png")


def autostart_path():
    """Where the "start at login" entry lives (its presence == start-at-boot on)."""
    return _config("autostart", "slow-coffee.desktop")


def autostart_enabled():
    return exists(autostart_path())


def autostart_start_active():
    """Read whether the existing autostart entry boots caffeinated.

    Parsed from its ``Exec`` line (no ``--start-inactive`` == caffeinated).
    Defaults to True when there is no entry to read.
    """
    try:
        with open(autostart_path()) as f:
            for line in f:
                if line.startswith("Exec="):
                    return "--start-inactive" not in line
    except OSError:
        pass
    return True


def set_autostart(enabled, start_active=True):
    """Create or remove the autostart entry. When creating, ``start_active``
    picks the Exec line so the next login honours "coffee at boot".
    Returns the path touched."""
    path = autostart_path()
    if enabled:
        os.makedirs(dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(_desktop_body(exec_cmd=_autostart_exec(start_active), autostart=True))
    elif exists(path):
        os.remove(path)
    return path


def install(autostart=False, start_active=True):
    """Install the launcher + icon (+ optional autostart). Returns paths written."""
    written = []

    icon_dst = _xdg("icons", "hicolor", "128x128", "apps", "slow-coffee.png")
    os.makedirs(dirname(icon_dst), exist_ok=True)
    shutil.copyfile(_src_icon(), icon_dst)
    written.append(icon_dst)

    desktop_dst = _xdg("applications", "slow-coffee.desktop")
    os.makedirs(dirname(desktop_dst), exist_ok=True)
    with open(desktop_dst, "w") as f:
        f.write(_desktop_body())
    written.append(desktop_dst)

    if autostart:
        written.append(set_autostart(True, start_active=start_active))

    # refresh the menu cache if the tool is around (best-effort)
    try:
        import subprocess
        subprocess.run(["update-desktop-database", _xdg("applications")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, FileNotFoundError):
        pass
    return written


def uninstall():
    """Remove everything install() created. Returns paths removed."""
    removed = []
    for path in (_xdg("icons", "hicolor", "128x128", "apps", "slow-coffee.png"),
                 _xdg("applications", "slow-coffee.desktop"),
                 _config("autostart", "slow-coffee.desktop")):
        if exists(path):
            os.remove(path)
            removed.append(path)
    return removed
