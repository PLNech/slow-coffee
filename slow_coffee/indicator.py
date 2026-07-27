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
suspend=4, both=12 - just OR them), and the freedesktop ScreenSaver interface
covers only the *screensaver* (it has no suspend concept at all). Neither,
however, is enough to survive a closed lid: logind's default
`LidSwitchIgnoreInhibited=yes` makes it ignore ordinary sleep inhibitors on
lid-close and honour only one that names `handle-lid-switch`. So whichever
session backend wins, a "sleep"/"both" request *also* spawns a
`systemd-inhibit --what=sleep:handle-lid-switch` companion process for the
sleep leg - that companion, not the session inhibitor, is what makes "sleep"
genuinely lid-close-safe. Backend 3 needs no companion: it *is* one, and its
`--what` already names the lid switch.

A fourth, *optional* trick rides on top: **slow mode**. A laptop kept awake with
its lid shut is a laptop cooking itself in a bag, so when the host provides a
thermal-mode helper (``/usr/local/sbin/thermal-mode-apply``, state in
``/etc/thermal-mode.conf``) slow-coffee can drop the machine into its quietest
mode while the lid is closed and put the previous mode back when it opens. While
engaged it also watches the CPU package temperature and, if the machine is
clearly losing the thermal fight, releases every inhibitor so it can suspend -
a safety valve beats a cooked mainboard. Going slow also blanks the panel: our
own idle inhibitor is precisely what stops the desktop from doing that on
lid-close, so a shut laptop would otherwise sit there lighting up its own
keyboard tray. Without that helper none of this appears in the menu and nothing
about the app's behaviour changes.

This is an independent implementation; see the README's "Prior art" note.
"""

import glob
import logging
import os
import subprocess
import time
from os.path import join, dirname, abspath, isdir

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, Gio, GLib, AyatanaAppIndicator3 as AppIndicator3

from . import __version__, APP_ID, desktop, settings

log = logging.getLogger(APP_ID)

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


# --- slow mode (optional, host-provided thermal helper) ---------------------
# Everything below is inert unless BOTH of these exist: the setuid-free sudo
# helper that switches thermal modes, and the world-readable file holding the
# current one. That keeps the public packages (AUR/deb) behaviour-identical on
# machines without the helper.
THERMAL_HELPER = "/usr/local/sbin/thermal-mode-apply"
THERMAL_CONF = "/etc/thermal-mode.conf"

# Modes to request when going slow, best first. "slow" is a sealed-space budget
# (~8 W) on hosts whose helper knows it; older helpers only know "silent"
# (~15 W), which field data showed is still too much heat for a closed bag over
# an hour. The sudoers whitelist makes an unknown mode fail fast and cleanly,
# so probing by trying is safe.
SLOW_THERMAL_MODES = ("slow", "silent")

# Discrete-GPU runtime power state. Read-only, purely advisory: if the dGPU is
# awake while we go slow, cooling capacity is being spent on it and the user
# deserves to know. slow-coffee never writes to nvidia devices.
DGPU_RUNTIME_STATUS = "/sys/bus/pci/devices/0000:01:00.0/power/runtime_status"

# Watchdog: sample temperatures this often while slow, and bail out (suspend
# the machine) after this many consecutive samples above either limit.
WATCHDOG_SECONDS = 30
WATCHDOG_LIMIT_C = 90.0
WATCHDOG_STRIKES = 3

PKG_TEMP_TYPE = "x86_pkg_temp"

# The die is the wrong thermometer for a laptop in a bag: the power cap keeps
# it comfortably under 90 C at 8-15 W while the *chassis* soaks past
# finger-safe with nowhere for the heat to go. Dell exposes a skin-temperature
# zone (type "TSKN") for exactly this. Calibration on the reference machine:
# ~58-60 C is a warm desk session, so the trip point sits above that with the
# same strike logic; sustained 68 C skin is a machine cooking in its own
# exhaust. Hosts without such a zone simply skip the skin check.
SKIN_TEMP_TYPE = "TSKN"
SKIN_LIMIT_C = 68.0

# Every watchdog sample is appended here while slow is engaged (CSV: ISO time,
# die C, skin C). The one overheat we know of left zero temperature telemetry
# behind, which turned its post-mortem into guesswork - cheap receipts since.
TELEMETRY_PATH = os.path.expanduser("~/.local/state/slow-coffee/thermal.csv")
TELEMETRY_MAX_BYTES = 1 << 20   # rotate to .1 past ~1 MB (months of samples)


def notify(summary, body=""):
    """Best-effort desktop notification; never raises, never blocks the loop."""
    try:
        Gio.Subprocess.new(["notify-send", "-a", "slow-coffee", summary, body],
                           Gio.SubprocessFlags.STDOUT_SILENCE |
                           Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error as err:
        log.debug("notify-send unavailable: %s", err)


# Screen blanking, tried in this order, then `xset` as the bare-X11 backstop.
# Neither D-Bus name is a sure thing: SetActive is not part of the freedesktop
# idle-inhibition spec at all (KDE implements it as an extension, GNOME answers
# NotSupported on that name), and gnome-shell's own SetActive has been observed
# not replying while a session is inhibited. Hence the short timeout below and a
# fallback chain that keeps going rather than trusting any single backend.
SCREENSAVER_BACKENDS = (
    ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
)

# Deliberately impatient: this runs on the lid-close path, inside the GTK main
# loop, so a wedged screensaver must cost us a moment - not four seconds of
# frozen tray while the machine we are trying to cool sits there lit up.
BLANK_TIMEOUT_MS = 1200

# One grumble per process is plenty if no backend will blank the screen.
_blank_warned = False


def blank_screen():
    """Turn the display off while the lid is shut. Returns the backend used.

    Slow mode has to ask for this explicitly: the idle inhibitor that keeps the
    machine awake is the same one that stops the desktop blanking on its own, so
    a closed lid would otherwise keep the panel lit for nothing.

    There is deliberately no matching un-blank. Every backend here wakes on user
    activity, and opening the lid to a keypress is exactly that - an explicit
    SetActive(false) would only race the user's own input.

    Returns the backend name, or None when nothing could blank the screen. That
    is a disappointment, never a failure: the machine is still awake, still slow.
    """
    global _blank_warned
    for name, path in SCREENSAVER_BACKENDS:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(name, path, name, "SetActive",
                          GLib.Variant("(b)", (True,)), None,
                          Gio.DBusCallFlags.NONE, BLANK_TIMEOUT_MS, None)
        except GLib.Error as err:
            log.info("%s would not blank the screen: %s", name, err)
            continue
        log.info("screen blanked via %s", name)
        return name

    # Bare X11 (no screensaver daemon on the bus): ask the server directly.
    # Pointless under Wayland, where DPMS isn't X's to hand out.
    if os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        try:
            proc = Gio.Subprocess.new(
                ["xset", "dpms", "force", "off"],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
            proc.wait(None)
        except GLib.Error as err:
            log.info("xset unavailable: %s", err)
        else:
            if proc.get_successful():
                log.info("screen blanked via xset dpms")
                return "xset dpms"

    log.warning("no backend could blank the screen")
    if not _blank_warned:
        _blank_warned = True
        notify("slow-coffee: screen stayed on",
               "Going slow worked, but nothing on this session would blank the "
               "display, so the closed lid keeps its panel lit.")
    return None


def slow_mode_available(helper=THERMAL_HELPER, conf=THERMAL_CONF):
    """True when this host has the thermal helper *and* a readable mode file."""
    return os.access(helper, os.X_OK) and os.access(conf, os.R_OK)


def read_thermal_mode(conf=THERMAL_CONF):
    """Return the ``MODE=`` value from the thermal conf, or None if unreadable."""
    try:
        with open(conf) as f:
            for line in f:
                line = line.strip()
                if line.startswith("MODE="):
                    return line[len("MODE="):].strip().strip('"\'') or None
    except OSError as err:
        log.warning("cannot read %s: %s", conf, err)
    return None


def find_zone_temp_path(zone_type, sysfs="/sys"):
    """Locate a thermal zone's temp file by *type name* rather than by number.

    Zone numbering is firmware order and shuffles between boots and machines.
    Returns a path in millidegrees Celsius, or None.
    """
    for type_path in sorted(glob.glob(join(sysfs, "class/thermal/thermal_zone*/type"))):
        try:
            with open(type_path) as f:
                if f.read().strip() == zone_type:
                    return join(dirname(type_path), "temp")
        except OSError:
            continue
    return None


def find_pkg_temp_path(sysfs="/sys"):
    """CPU package temperature: the x86_pkg_temp zone, else coretemp hwmon."""
    path = find_zone_temp_path(PKG_TEMP_TYPE, sysfs)
    if path:
        return path
    for name_path in sorted(glob.glob(join(sysfs, "class/hwmon/hwmon*/name"))):
        try:
            with open(name_path) as f:
                if f.read().strip() == "coretemp":
                    return join(dirname(name_path), "temp1_input")
        except OSError:
            continue
    return None


def read_temp_c(path):
    """Read a sysfs millidegree file as degrees Celsius, or None."""
    try:
        with open(path) as f:
            return int(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def log_telemetry(die_c, skin_c, path=TELEMETRY_PATH):
    """Append one temperature sample; best-effort, never raises."""
    fmt = lambda v: "" if v is None else "%.1f" % v
    try:
        os.makedirs(dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > TELEMETRY_MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a") as f:
            f.write("%s,%s,%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    fmt(die_c), fmt(skin_c)))
    except OSError as err:
        log.debug("telemetry write failed: %s", err)


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
    Everything else can only inhibit the screensaver, never suspend. Either way,
    any mode with a sleep leg ("sleep"/"both") also gets a companion
    systemd-inhibit process holding ``sleep:handle-lid-switch`` - session-level
    suspend inhibitors do not survive a lid-close, see acquire() - tracked
    separately so release() can tear down both halves. The systemd backend needs
    no companion: its own --what already names the lid switch.
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
            # A session-level suspend inhibit is NOT a lid-close inhibit: logind
            # ships with LidSwitchIgnoreInhibited=yes, so on lid-close it steps
            # over ordinary sleep inhibitors and only respects one that names
            # handle-lid-switch. Field evidence: coffee on, lid shut, "Lid
            # closed." and "Suspending..." in the same journal second. So the
            # sleep leg gets the same logind companion the other backends use.
            if sleep:
                self._spawn_systemd("sleep:handle-lid-switch")
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


class LidWatcher:
    """Reports lid open/close from logind's ``LidClosed`` property.

    logind is the one component that knows the lid state regardless of desktop,
    and it already emits a standard ``PropertiesChanged`` on the Manager object -
    no polling, no ACPI file watching. The initial value is read once at start
    so a lid that is *already* shut is not mistaken for "open".
    """

    NAME = "org.freedesktop.login1"
    PATH = "/org/freedesktop/login1"
    IFACE = "org.freedesktop.login1.Manager"

    # Some lid switches bounce: the reference machine's kernel flags its lid
    # device as "not compliant to SW_LID" and logind has recorded phantom
    # close/open pairs seconds apart. Believe a transition only once it has
    # held this long.
    DEBOUNCE_SECONDS = 2

    def __init__(self, on_change):
        self.on_change = on_change      # called with True (closed) / False (open)
        self.closed = False
        self.sub_id = None
        self._settle_id = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error as err:
            log.info("no system bus, lid watching disabled: %s", err)
            self.bus = None
            return
        self.closed = bool(self._read_lid())
        self.sub_id = self.bus.signal_subscribe(
            self.NAME, "org.freedesktop.DBus.Properties", "PropertiesChanged",
            self.PATH, None, Gio.DBusSignalFlags.NONE, self._on_properties)

    def _read_lid(self):
        try:
            res = self.bus.call_sync(
                self.NAME, self.PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (self.IFACE, "LidClosed")),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 4000, None)
            return res.unpack()[0]
        except GLib.Error as err:
            log.info("cannot read LidClosed: %s", err)
            return False

    def _on_properties(self, _conn, _sender, _path, _iface, _signal, params):
        iface, changed, invalidated = params.unpack()
        if iface != self.IFACE:
            return
        if "LidClosed" in changed:
            closed = bool(changed["LidClosed"])
        elif "LidClosed" in invalidated:
            closed = bool(self._read_lid())
        else:
            return
        if closed == self.closed:
            return
        # Restart the settle clock on every flap; only a state that survives it
        # is reported. A real close still acts within DEBOUNCE_SECONDS.
        if self._settle_id is not None:
            GLib.source_remove(self._settle_id)
        self._settle_id = GLib.timeout_add_seconds(self.DEBOUNCE_SECONDS,
                                                   self._settle)

    def _settle(self):
        self._settle_id = None
        closed = bool(self._read_lid())
        if closed != self.closed:
            self.closed = closed
            log.info("lid %s (held %ds)", "closed" if closed else "opened",
                     self.DEBOUNCE_SECONDS)
            self.on_change(closed)
        else:
            log.info("lid bounce ignored (state back to %s)",
                     "closed" if closed else "open")
        return False

    def stop(self):
        if self.sub_id is not None:
            self.bus.signal_unsubscribe(self.sub_id)
            self.sub_id = None
        if self._settle_id is not None:
            GLib.source_remove(self._settle_id)
            self._settle_id = None


class SlowMode:
    """Puts the machine in its quietest thermal mode, and watches it there.

    Engaging snapshots the current mode, asks the host helper for ``silent``
    (via ``sudo -n``, so a missing sudoers rule fails fast instead of hanging on
    a password prompt), then samples the CPU package temperature every
    ``WATCHDOG_SECONDS``. Releasing restores the snapshot - but only if the
    persisted mode is *still* ``silent``: if the user switched modes underneath
    us, theirs wins.

    ``on_overheat`` is invoked when the watchdog gives up, so the caller can drop
    its inhibitors and let the machine suspend.
    """

    def __init__(self, on_overheat=None, helper=THERMAL_HELPER, conf=THERMAL_CONF):
        self.helper = helper
        self.conf = conf
        self.on_overheat = on_overheat
        self.available = slow_mode_available(helper, conf)
        self.engaged = False
        self.previous = None        # mode to restore, None = nothing to restore
        self.mode = None            # the slow mode we actually got
        self.watchdog_id = None
        self.strikes = 0

    def engage(self):
        if not self.available or self.engaged:
            return self.engaged
        current = read_thermal_mode(self.conf)
        if current is None:
            log.warning("no current thermal mode to snapshot; staying as-is")
            return False
        self.mode = None
        for mode in SLOW_THERMAL_MODES:
            if current == mode:
                self.mode = mode
                self.previous = None    # already quiet: leave it be on release
                break
            if self._apply(mode):
                self.mode = mode
                self.previous = current
                break
        if self.mode is None:
            notify("slow-coffee: could not go slow",
                   "The thermal helper refused every slow mode. "
                   "Staying in %s." % current)
            return False
        self.engaged = True
        log.info("slow mode engaged: %s (was %s)", self.mode, current)
        self._warn_if_dgpu_awake()
        self.strikes = 0
        self.watchdog_id = GLib.timeout_add_seconds(WATCHDOG_SECONDS, self._sample)
        return True

    def release(self):
        if not self.engaged:
            return
        self.engaged = False
        if self.watchdog_id is not None:
            GLib.source_remove(self.watchdog_id)
            self.watchdog_id = None
        previous, self.previous = self.previous, None
        mode, self.mode = self.mode, None
        if previous is None:
            return
        now = read_thermal_mode(self.conf)
        if now != mode:
            log.info("thermal mode is %s, not ours - leaving it alone", now)
            return
        if self._apply(previous):
            log.info("slow mode released, restored %s", previous)

    def _apply(self, mode):
        """Run the host helper. No shell, exact argument, non-interactive sudo."""
        try:
            proc = Gio.Subprocess.new(
                ["sudo", "-n", self.helper, mode],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE)
            _ok, _out, err = proc.communicate_utf8(None, None)
        except GLib.Error as exc:
            log.warning("cannot run %s: %s", self.helper, exc)
            return False
        if proc.get_successful():
            return True
        log.warning("%s %s failed: %s", self.helper, mode, (err or "").strip())
        return False

    def _warn_if_dgpu_awake(self):
        status = None
        try:
            with open(DGPU_RUNTIME_STATUS) as f:
                status = f.read().strip()
        except OSError:
            return                  # no dGPU (or no runtime PM): nothing to say
        if status != "suspended":
            log.info("dGPU runtime status is %s while going slow", status)
            notify("slow-coffee: dGPU is awake",
                   "The discrete GPU is %s, so the lid-closed machine will run "
                   "hotter than slow mode intends." % status)

    def _sample(self):
        if not self.engaged:
            return False
        die_path = find_pkg_temp_path()
        skin_path = find_zone_temp_path(SKIN_TEMP_TYPE)
        die = read_temp_c(die_path) if die_path else None
        skin = read_temp_c(skin_path) if skin_path else None
        log_telemetry(die, skin)
        if die is None and skin is None:
            return True             # no sensors: nothing to judge, keep going

        # Two thermometers, one verdict: the die catches a runaway CPU, the
        # skin catches slow heat-soak in a sealed bag - the failure mode a
        # power-capped die never reports.
        over = None
        if die is not None and die > WATCHDOG_LIMIT_C:
            over = "CPU die at %.0f C (limit %.0f)" % (die, WATCHDOG_LIMIT_C)
        elif skin is not None and skin > SKIN_LIMIT_C:
            over = "chassis skin at %.0f C (limit %.0f)" % (skin, SKIN_LIMIT_C)
        if over is None:
            self.strikes = 0
            return True
        self.strikes += 1
        log.warning("%s - strike %d/%d", over, self.strikes, WATCHDOG_STRIKES)
        if self.strikes < WATCHDOG_STRIKES:
            return True
        self.watchdog_id = None
        notify("slow-coffee: overheating - forcing sleep",
               "%s for %d samples in a row. Coffee is off and the machine is "
               "suspending. Cooling beats staying awake." %
               (over, WATCHDOG_STRIKES))
        if self.on_overheat is not None:
            self.on_overheat()
        return False


class SlowCoffee:
    def __init__(self, start_active=True, minutes=0):
        self.inhibitor = Inhibitor()
        self.timer_id = None
        self._syncing = False      # guards the "Slow now" checkbox against echo
        self.icons = find_icons_dir()
        # Seed "coffee at boot" from any pre-existing autostart entry so the
        # checkbox reflects reality on first run rather than clobbering it.
        self.settings = settings.load(
            seed={"coffee_at_boot": desktop.autostart_start_active()})

        # Slow mode and its lid watcher only exist where the host helper does.
        self.slow = SlowMode(on_overheat=self.on_overheat)
        self.lid = LidWatcher(self.on_lid) if self.slow.available else None

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

    # --- slow mode --------------------------------------------------------
    def _go_slow(self):
        """Engage slow mode, and blank the panel if the lid is really shut.

        The lid check is the whole point: "Slow now" is also reachable with the
        laptop open in front of the user, and blacking out the screen they are
        looking at would be a rude way to save a few watts.
        """
        if not self.slow.engage():
            return False
        if self.lid is not None and self.lid.closed:
            blank_screen()
        return True

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

        self.slow_closed_item = self.slow_now_item = None
        if self.slow.available:
            menu.append(Gtk.SeparatorMenuItem())

            self.slow_closed_item = Gtk.CheckMenuItem(label="Slow when lid closed")
            self.slow_closed_item.set_active(self.settings["slow_when_closed"])
            self.slow_closed_item.connect("toggled", self.on_toggle_slow_when_closed)
            menu.append(self.slow_closed_item)

            self.slow_now_item = Gtk.CheckMenuItem(label="Slow now")
            self.slow_now_item.set_active(self.slow.engaged)
            self.slow_now_item.connect("toggled", self.on_toggle_slow_now)
            menu.append(self.slow_now_item)

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
        # Caffeinating a machine whose lid is *already* shut (docked, or an
        # autostart under a closed lid) deserves the same treatment as closing it.
        if (backend and self.lid is not None and self.lid.closed
                and self.settings["slow_when_closed"]):
            self._go_slow()
        self._refresh(backend)

    def deactivate(self):
        self._clear_timer()
        self.inhibitor.release()
        self.slow.release()         # no coffee, no reason to hold the machine slow
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
        slow = " - slow" if self.slow.engaged else ""
        self.ind.set_title("slow-coffee: %s%s%s"
                           % ("awake" if on else "off", via, slow))
        if self.slow_now_item is not None:
            # Reflect reality without re-entering the toggled handler.
            self._syncing = True
            self.slow_now_item.set_active(self.slow.engaged)
            self._syncing = False

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

    def on_toggle_slow_when_closed(self, item):
        self.settings["slow_when_closed"] = item.get_active()
        settings.save(self.settings)
        # Turning it off with the lid already shut un-slows right away; turning
        # it on in that state goes slow now rather than at the next lid-close.
        if self.lid is not None and self.lid.closed and self.inhibitor.active:
            self._go_slow() if item.get_active() else self.slow.release()
            self._refresh()

    def on_toggle_slow_now(self, item):
        if self._syncing:
            return
        if item.get_active():
            self._go_slow()
        else:
            self.slow.release()
        self._refresh()

    def on_lid(self, closed):
        """Lid closed while caffeinated: go slow. Lid opened: back to normal."""
        if closed:
            if self.inhibitor.active and self.settings["slow_when_closed"]:
                self._go_slow()
        else:
            self.slow.release()
        self._refresh()

    def on_overheat(self):
        """Watchdog safety valve: drop the coffee and FORCE the suspend.

        Releasing our own inhibitors is not enough: any other block inhibitor
        on the system (another caffeine app, a stray systemd-inhibit) would
        veto a polite suspend and the machine would keep cooking - which is
        how the one real overheat played out. ``systemctl suspend -i``
        ignores inhibitors outright; when the stakes are a cooked mainboard,
        rudeness is correct.
        """
        log.warning("thermal watchdog tripped - releasing inhibitors, forcing suspend")
        self.deactivate()
        try:
            Gio.Subprocess.new(["systemctl", "suspend", "-i"],
                               Gio.SubprocessFlags.STDOUT_SILENCE |
                               Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error as err:
            log.error("forced suspend failed: %s - machine stays hot!", err)
            notify("slow-coffee: SUSPEND FAILED",
                   "The thermal watchdog could not suspend the machine. "
                   "It is overheating: intervene now.")

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
        self.deactivate()          # also restores the pre-slow thermal mode
        if self.lid is not None:
            self.lid.stop()
        Gtk.main_quit()
