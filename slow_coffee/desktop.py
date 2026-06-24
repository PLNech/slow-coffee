"""Self-integration: drop a .desktop entry + icon into the user's XDG dirs.

This is what makes ``slow-coffee`` appear in "installed applications" even from a
``pip install --user`` - no root, no distro package required. Distro packages
(deb/AUR) install the same files system-wide via their own rules instead.
"""

import os
import shutil
from os.path import join, dirname, abspath, exists

from .indicator import APP_ID

DESKTOP_BODY = """\
[Desktop Entry]
Type=Application
Name=Slow Coffee
GenericName=Keep Awake
Comment=Keep your screen awake - on by default, sip at your own pace
Exec=slow-coffee
Icon=slow-coffee
Terminal=false
Categories=Utility;GTK;
Keywords=caffeine;caffeinate;awake;idle;screensaver;inhibit;presentation;
StartupNotify=false
"""

AUTOSTART_EXTRA = "X-GNOME-Autostart-enabled=true\n"


def _xdg(*parts):
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return join(base, *parts)


def _config(*parts):
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return join(base, *parts)


def _src_icon():
    return join(dirname(abspath(__file__)), "icons", "slow-coffee-full.png")


def install(autostart=False):
    """Install the launcher + icon (+ optional autostart). Returns paths written."""
    written = []

    icon_dst = _xdg("icons", "hicolor", "128x128", "apps", "slow-coffee.png")
    os.makedirs(dirname(icon_dst), exist_ok=True)
    shutil.copyfile(_src_icon(), icon_dst)
    written.append(icon_dst)

    desktop_dst = _xdg("applications", "slow-coffee.desktop")
    os.makedirs(dirname(desktop_dst), exist_ok=True)
    with open(desktop_dst, "w") as f:
        f.write(DESKTOP_BODY)
    written.append(desktop_dst)

    if autostart:
        auto_dst = _config("autostart", "slow-coffee.desktop")
        os.makedirs(dirname(auto_dst), exist_ok=True)
        with open(auto_dst, "w") as f:
            f.write(DESKTOP_BODY + AUTOSTART_EXTRA)
        written.append(auto_dst)

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
