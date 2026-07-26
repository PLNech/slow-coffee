"""The tray indicator and the idle/sleep-inhibition backends.

Inhibition is requested over D-Bus, preferring the running session daemon and
falling back to logind:

    1. org.gnome.SessionManager.Inhibit   (GNOME; X11 verified, Wayland expected)
    2. org.freedesktop.ScreenSaver.Inhibit (KDE / freedesktop)
    3. systemd-inhibit --what=...          (logind fallback for bare WMs)

Three selectable modes, from the tray menu:

    - **Idle only** (default, safest): screen stays on, but closing the lid or
      a power-button suspend still sleeps the laptop.
    - **Sleep only**: the screen can still blank/lock on its own, but the
      machine itself won't suspend - including on lid-close.
    - **Both**: neither idle-blanking nor suspend happens.

GNOME's SessionManager covers both axes with one Inhibit flag (idle=8,
suspend=4, both=12 - just OR them). The freedesktop ScreenSaver interface can
only ever inhibit the *screensaver* (no suspend concept), so on that backend -
or on a bare WM with neither D-Bus service - a "sleep"/"both" request also
spawns a `systemd-inhibit --what=sleep:handle-lid-switch` companion process
for the sleep leg. `handle-lid-switch` is included so "sleep" genuinely means
lid-close-safe, matching what "both" promises.

This is an independent implementation; see the README's "Prior art" note.
"""

import os
import subprocess
from os.path import join, dirname, abspath, isdir

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, Gio, GLib, AyatanaAppIndicator3 as AppIndicator3

from . import __version__, APP_ID, desktop, settings

REASON = "User asked to stay awake (slow-coffee)"

# GNOME SessionManager inhibit flags: 8 = "session marked idle" (screen stays
# on), 4 = "suspend" (covers logout-triggered *and* lid-close-triggered sleep).
# They combine by OR: idle-only=8, sleep-only=4, both=12.
GSM_INHIBIT_IDLE = 8
GSM_INHIBIT_SUSPEND = 4

# systemd-inhibit --what= value per mode; sleep modes include the lid switch
# so "sleep" isn't quietly defeated by closing the laptop.
SYSTEMD_WHAT = {
    "idle": "idle",
    "sleep": "sleep:handle-lid-switch",
    "both": "idle:sleep:handle-lid-switch",
}

# (label, minutes) for the duration radio group; 0 == indefinitely
DURATIONS = (("Indefinitely", 0), ("30 minutes", 30), ("1 hour", 60), ("2 hours", 120))

# (label, mode) for the inhibit-mode radio group
MODES = (("Idle only (screen stays on)", "idle"),
          ("Sleep only (lid-close safe)", "sleep"),
          ("Both", "both"))


def find_icons_dir():
    """Locate the cup icons, whether pip-installed or run from a checkout."""
    here = dirname(abspath(__file__))
    for path in (join(here, "icons"),
                 join("/usr/share", APP_ID, "icons"),
                 join(os.path.expanduser("~/.local/share"), APP_ID, "icons")):
        if isdir(path):
            return path
    return here


class Inhibitor:
    """Holds at most one active inhibition (idle/sleep/both), trying backends
    in order of preference.

    One persistent session-bus connection is kept for the whole run: the
    freedesktop ScreenSaver inhibit is bound to the D-Bus sender, so dropping the
    connection would silently release it.

    GNOME's SessionManager covers idle and suspend in one call (OR the flags).
    Everything else can only inhibit the screensaver, never suspend - a "sleep"
    or "both" request there gets a companion systemd-inhibit process for the
    sleep leg, tracked separately so release() can tear down both halves.
    """

    def __init__(self):
        self.backend = None        # "gnome" | "screensaver" | "systemd"
        self.mode = None           # "idle" | "sleep" | "both", once active
        self.cookie = None         # for the D-Bus backends
        self.proc = None           # for the systemd-inhibit child (own backend
                                    # OR the sleep-leg companion under screensaver)
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.bus = None

    @property
    def active(self):
        return self.backend is not None

    def _dbus_inhibit(self, name, path, iface, method, args, sig):
        if self.bus is None:
            return None
        try:
            res = self.bus.call_sync(
                name, path, iface, method,
                GLib.Variant(sig, args), GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE, 4000, None)
            return res.unpack()[0]
        except GLib.Error:
            return None

    def _dbus_uninhibit(self, name, path, iface, method, cookie):
        try:
            self.bus.call_sync(
                name, path, iface, method,
                GLib.Variant("(u)", (cookie,)), None,
                Gio.DBusCallFlags.NONE, 4000, None)
        except GLib.Error:
            pass

    def acquire(self, mode="idle"):
        if self.active:
            return self.backend
        idle = mode in ("idle", "both")
        sleep = mode in ("sleep", "both")
        flag = (GSM_INHIBIT_IDLE if idle else 0) | (GSM_INHIBIT_SUSPEND if sleep else 0)
        cookie = self._dbus_inhibit(
            "org.gnome.SessionManager", "/org/gnome/SessionManager",
            "org.gnome.SessionManager", "Inhibit",
            (APP_ID, 0, REASON, flag), "(susu)")
        if cookie is not None:
            self.backend, self.cookie, self.mode = "gnome", cookie, mode
            return self.backend

        # freedesktop ScreenSaver has no concept of "suspend" - it only ever
        # covers the idle leg, so take it when idle is wanted but still spawn
        # a systemd-inhibit companion below if sleep is also wanted.
        if idle:
            cookie = self._dbus_inhibit(
                "org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
                "org.freedesktop.ScreenSaver", "Inhibit",
                (APP_ID, REASON), "(ss)")
            if cookie is not None:
                self.backend, self.cookie, self.mode = "screensaver", cookie, mode
                if sleep:
                    self._spawn_systemd("sleep:handle-lid-switch")
                return self.backend

        # Bare WM (or ScreenSaver unavailable): systemd-inhibit is the only
        # backend left, and it can cover idle/sleep/both in a single process.
        if self._spawn_systemd(SYSTEMD_WHAT[mode]):
            self.backend, self.mode = "systemd", mode
            return self.backend
        return None

    def _spawn_systemd(self, what):
        try:
            self.proc = subprocess.Popen(
                ["systemd-inhibit", "--what=" + what, "--who=slow-coffee",
                 "--why=" + REASON, "--mode=block", "sleep", "infinity"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (OSError, FileNotFoundError):
            self.proc = None
            return False

    def release(self):
        if self.backend == "gnome":
            self._dbus_uninhibit(
                "org.gnome.SessionManager", "/org/gnome/SessionManager",
                "org.gnome.SessionManager", "Uninhibit", self.cookie)
        elif self.backend == "screensaver":
            self._dbus_uninhibit(
                "org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
                "org.freedesktop.ScreenSaver", "UnInhibit", self.cookie)
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.backend = self.mode = self.cookie = self.proc = None


class SlowCoffee:
    def __init__(self, start_active=True, minutes=0):
        self.inhibitor = Inhibitor()
        self.timer_id = None
        self.icons = find_icons_dir()
        # Seed "coffee at boot" from any pre-existing autostart entry so the
        # checkbox reflects reality on first run rather than clobbering it.
        self.settings = settings.load(
            seed={"coffee_at_boot": desktop.autostart_start_active()})

        self.ind = AppIndicator3.Indicator.new_with_path(
            APP_ID, "slow-coffee-empty",
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS, self.icons)
        self.ind.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.ind.set_title("slow-coffee")
        self.ind.set_menu(self._build_menu())

        if start_active:
            self.activate(minutes)
        else:
            self._refresh()

    # --- menu -------------------------------------------------------------
    def _build_menu(self):
        menu = Gtk.Menu()

        self.toggle_item = Gtk.MenuItem(label="Deactivate")
        self.toggle_item.connect("activate", self.on_toggle)
        menu.append(self.toggle_item)

        menu.append(Gtk.SeparatorMenuItem())

        self.mode_items = []
        for label, mode in MODES:
            item = Gtk.RadioMenuItem(label=label)
            if self.mode_items:
                item.join_group(self.mode_items[0])
            item.set_active(mode == "idle")
            item.connect("activate", self.on_pick_mode, mode)
            self.mode_items.append(item)
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        self.duration_items = []
        for label, mins in DURATIONS:
            item = Gtk.RadioMenuItem(label=label)
            if self.duration_items:
                item.join_group(self.duration_items[0])
            item.set_active(mins == 0)
            item.connect("activate", self.on_pick_duration, mins)
            self.duration_items.append(item)
            menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        self.boot_item = Gtk.CheckMenuItem(label="Start at boot")
        self.boot_item.set_active(desktop.autostart_enabled())
        self.boot_item.connect("toggled", self.on_toggle_boot)
        menu.append(self.boot_item)

        self.coffee_boot_item = Gtk.CheckMenuItem(label="Coffee at boot")
        self.coffee_boot_item.set_active(self.settings["coffee_at_boot"])
        self.coffee_boot_item.connect("toggled", self.on_toggle_coffee_boot)
        menu.append(self.coffee_boot_item)

        menu.append(Gtk.SeparatorMenuItem())

        about = Gtk.MenuItem(label="About slow-coffee")
        about.connect("activate", self.on_about)
        menu.append(about)

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self.on_quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    # --- state ------------------------------------------------------------
    def _clear_timer(self):
        if self.timer_id is not None:
            GLib.source_remove(self.timer_id)
            self.timer_id = None

    def activate(self, minutes=0, mode=None):
        backend = self.inhibitor.acquire(mode or self._selected_mode())
        self._clear_timer()
        if minutes > 0:
            self.timer_id = GLib.timeout_add_seconds(minutes * 60, self._expire)
        self._refresh(backend)

    def deactivate(self):
        self._clear_timer()
        self.inhibitor.release()
        self._refresh()

    def _expire(self):
        self.timer_id = None
        self.deactivate()
        if self.duration_items:
            self.duration_items[0].set_active(True)
        return False  # one-shot

    def _refresh(self, backend=None):
        on = self.inhibitor.active
        self.ind.set_icon_full(
            "slow-coffee-full" if on else "slow-coffee-empty",
            "caffeinated" if on else "decaf")
        self.toggle_item.set_label("Deactivate" if on else "Activate")
        via = " via %s (%s)" % (backend, self.inhibitor.mode) if backend else ""
        self.ind.set_title("slow-coffee: %s%s" % ("awake" if on else "off", via))

    # --- callbacks --------------------------------------------------------
    def on_toggle(self, _item):
        if self.inhibitor.active:
            self.deactivate()
        else:
            self.activate(self._selected_minutes())

    def on_pick_duration(self, item, minutes):
        if item.get_active():
            self.activate(minutes)

    def on_pick_mode(self, item, mode):
        # A running inhibitor's flags are set at acquire time - switching mode
        # while active means release-then-reacquire, not just changing state.
        # (Restarts the selected timer duration rather than tracking the
        # precise remainder - switching mode mid-timer is a rare edge case.)
        if item.get_active() and self.inhibitor.active:
            self.deactivate()
            self.activate(self._selected_minutes(), mode)

    def on_toggle_boot(self, item):
        # Presence of the autostart entry IS the "start at boot" state; write it
        # with the Exec line that honours the current "coffee at boot" choice.
        desktop.set_autostart(item.get_active(),
                              start_active=self.settings["coffee_at_boot"])

    def on_toggle_coffee_boot(self, item):
        self.settings["coffee_at_boot"] = item.get_active()
        settings.save(self.settings)
        # If we already start at boot, rewrite the entry so the next login obeys.
        if desktop.autostart_enabled():
            desktop.set_autostart(True, start_active=item.get_active())

    def _selected_minutes(self):
        for item, (_label, mins) in zip(self.duration_items, DURATIONS):
            if item.get_active():
                return mins
        return 0

    def _selected_mode(self):
        for item, (_label, mode) in zip(self.mode_items, MODES):
            if item.get_active():
                return mode
        return "idle"

    def on_about(self, _item):
        d = Gtk.AboutDialog()
        d.set_program_name("slow-coffee")
        d.set_version(__version__)
        d.set_comments("A take-your-time keep-awake indicator.\n"
                       "On by default; sips D-Bus, not your battery.")
        d.set_website("https://github.com/PLNech/slow-coffee")
        d.set_license_type(Gtk.License.MIT_X11)
        d.run()
        d.destroy()

    def on_quit(self, _item):
        self.deactivate()
        Gtk.main_quit()
