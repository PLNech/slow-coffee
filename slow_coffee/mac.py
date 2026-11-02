"""macOS menu-bar build of slow-coffee.

Same idea as the Linux tray - cup boots full, stays awake until told otherwise -
on macOS's own machinery instead of D-Bus:

    - **Idle only**  ``caffeinate -d -i``: display and idle sleep held off. Closing
      the lid still sleeps the Mac (Apple's behaviour, not ours).
    - **Stay awake** ``caffeinate -i -s``: the machine keeps running with the
      screen free to blank. ``-s`` only holds on AC power, and still yields to a lid
      close.
    - **Lid closed OK** the one that survives a shut lid, battery included.
      macOS has exactly one switch for that - ``pmset disablesleep`` - and it
      needs root and *persists across reboots*, so it gets guard rails: one admin
      prompt installs a tiny root watcher that flips it back off the moment this
      app dies (crash, kill -9, logout) or the cup is emptied, plus a low-battery
      cut-off. The cup never leaves the Mac in a sleepless state behind it.

Every ``caffeinate`` child is tied to our pid with ``-w``, so the OS releases it
if we vanish. Everything is released on quit.
"""

import atexit
import logging
import os
import plistlib
import signal
import subprocess
import sys
from os.path import dirname, abspath, join, exists

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

from . import __version__, APP_ID, settings

log = logging.getLogger(APP_ID)

REASON = "slow-coffee"

# (label, mode) for the inhibit-mode radio group
MODES = (("Idle only (screen stays on)", "idle"),
         ("Stay awake (screen may sleep)", "sleep"),
         ("Lid closed OK (asks for admin)", "lid"))

CAFFEINATE_FLAGS = {
    "idle": ["-d", "-i"],
    "sleep": ["-i", "-s"],
    "lid": ["-i", "-s"],
}

# (label, minutes); 0 == indefinitely
DURATIONS = (("Indefinitely", 0), ("30 minutes", 30), ("1 hour", 60), ("2 hours", 120))

# Lid-closed mode on battery: a shut Mac in a bag cannot shed heat, and a dead
# battery loses the work we were keeping awake for. Cut the lid mode here.
LOW_BATTERY_PERCENT = 15
TICK_SECONDS = 5

LAUNCH_LABEL = "com.plnech.slow-coffee"
LAUNCH_AGENT = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LAUNCH_LABEL)
STATE_DIR = os.path.expanduser("~/Library/Application Support/slow-coffee")
LID_FLAG = join(STATE_DIR, "lid.flag")      # present == lid mode wanted
LOG_PATH = os.path.expanduser("~/Library/Logs/slow-coffee.log")


def icons_dir():
    return join(dirname(abspath(__file__)), "icons")


def sleep_disabled():
    """True when ``pmset disablesleep`` is currently on (readable without root)."""
    try:
        out = subprocess.run(["pmset", "-g"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == "SleepDisabled":
            return parts[-1] == "1"
    return False


def battery():
    """Return (percent, on_ac) from ``pmset -g batt``, or (None, True) with no battery."""
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None, True
    on_ac = "AC Power" in out
    for token in out.replace(";", " ").split():
        if token.endswith("%") and token[:-1].isdigit():
            return int(token[:-1]), on_ac
    return None, on_ac


def _admin(shell_script):
    """Run a shell script as root behind the standard macOS admin prompt."""
    escaped = shell_script.replace("\\", "\\\\").replace('"', '\\"')
    applescript = 'do shell script "%s" with administrator privileges' % escaped
    proc = subprocess.run(["osascript", "-e", applescript],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        log.warning("admin command refused: %s", proc.stderr.strip())
    return proc.returncode == 0


PMSET = "/usr/bin/pmset"


def watcher_command(pid, flag, pmset=PMSET, interval=3):
    """Shell snippet for the root dead-man's switch: restore sleep when ``pid``
    dies or ``flag`` disappears. Backgrounded, so it outlives the admin prompt."""
    return (
        "nohup /bin/sh -c 'while kill -0 %(pid)d 2>/dev/null && [ -e \"%(flag)s\" ]; "
        "do sleep %(interval)s; done; %(pmset)s -a disablesleep 0' "
        ">/dev/null 2>&1 &"
    ) % {"pid": pid, "flag": flag.replace("'", ""), "pmset": pmset,
         "interval": interval}


def enable_lid_mode():
    """Turn ``pmset disablesleep`` on, with a root watcher that turns it off again.

    The watcher outlives this process on purpose: it is the dead-man's switch.
    It polls our pid and the flag file, and restores sleep when either goes away.
    The flag file is user-writable, which is fine - the only thing writing to it
    (or deleting it) can do is *re-enable* sleep.
    """
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LID_FLAG, "w") as f:
        f.write(str(os.getpid()))
    script = "%s -a disablesleep 1; %s" % (
        PMSET, watcher_command(os.getpid(), LID_FLAG))
    ok = _admin(script)
    if not ok:
        _remove_flag()
    return ok


def _remove_flag():
    try:
        os.remove(LID_FLAG)
    except OSError:
        pass


def disable_lid_mode():
    """Ask the watcher to restore sleep (no second password prompt needed)."""
    _remove_flag()


class SlowCoffeeMac(rumps.App):
    def __init__(self, start_active=True, minutes=0):
        super().__init__("slow-coffee", icon=join(icons_dir(), "slow-coffee-empty.png"),
                         template=False, quit_button=None)
        self.proc = None            # caffeinate child
        self.mode = None            # mode currently held, None == off
        self.lid_on = False
        self.expire_timer = None
        self.minutes = minutes
        self.settings = settings.load()
        self.selected_mode = "idle"
        self.selected_minutes = 0

        self.toggle_item = rumps.MenuItem("Deactivate", callback=self.on_toggle)
        self.mode_items = {}
        mode_menu = []
        for label, mode in MODES:
            item = rumps.MenuItem(label, callback=self._make_mode_cb(mode))
            item.state = mode == self.selected_mode
            self.mode_items[mode] = item
            mode_menu.append(item)
        self.duration_items = {}
        duration_menu = []
        for label, mins in DURATIONS:
            item = rumps.MenuItem(label, callback=self._make_duration_cb(mins))
            item.state = mins == 0
            self.duration_items[mins] = item
            duration_menu.append(item)

        self.login_item = rumps.MenuItem("Start at login", callback=self.on_toggle_login)
        self.login_item.state = exists(LAUNCH_AGENT)
        self.coffee_login_item = rumps.MenuItem("Coffee at login", callback=self.on_toggle_coffee_login)
        self.coffee_login_item.state = self.settings["coffee_at_boot"]

        self.menu = [self.toggle_item, None, *mode_menu, None, *duration_menu, None,
                     self.login_item, self.coffee_login_item, None,
                     rumps.MenuItem("About slow-coffee", callback=self.on_about),
                     rumps.MenuItem("Quit", callback=self.on_quit)]

        atexit.register(self.deactivate)
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, self._on_signal)
        # Keeps Python's signal handlers serviced inside the Cocoa loop, and
        # drives the battery watchdog.
        self.tick = rumps.Timer(self.on_tick, TICK_SECONDS)
        self.tick.start()

        # A previous run that was SIGKILLed may have left sleep disabled; the root
        # watcher normally cleans that up, so only warn about the stragglers.
        if sleep_disabled() and not exists(LID_FLAG):
            log.warning("SleepDisabled is on but no slow-coffee lid flag exists "
                        "(set outside slow-coffee? undo: sudo pmset -a disablesleep 0)")

        if start_active:
            self.activate(minutes)
        else:
            self._refresh()

    # --- state ------------------------------------------------------------
    def activate(self, minutes=0, mode=None):
        self.deactivate()
        mode = mode or self.selected_mode
        if mode == "lid":
            if not enable_lid_mode():
                rumps.notification("slow-coffee", "Lid-closed mode not enabled",
                                   "Admin approval was cancelled; staying in 'Stay awake'.")
                mode = "sleep"
            else:
                self.lid_on = True
        try:
            self.proc = subprocess.Popen(
                ["caffeinate"] + CAFFEINATE_FLAGS[mode] + ["-w", str(os.getpid())],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.mode = mode
        except OSError as err:
            log.error("cannot run caffeinate: %s", err)
            self.mode = None
            if self.lid_on:
                disable_lid_mode()
                self.lid_on = False
        if self.mode and minutes > 0:
            self.expire_timer = rumps.Timer(self._expire, minutes * 60)
            self.expire_timer.start()
        self._refresh()

    def deactivate(self):
        if self.expire_timer is not None:
            self.expire_timer.stop()
            self.expire_timer = None
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        if self.lid_on:
            disable_lid_mode()
            self.lid_on = False
        self.mode = None
        if getattr(self, "toggle_item", None) is not None:
            self._refresh()

    def _expire(self, _timer):
        self.deactivate()
        self._select_duration(0)

    def _refresh(self):
        on = self.mode is not None
        self.icon = join(icons_dir(), "slow-coffee-full.png" if on else "slow-coffee-empty.png")
        self.toggle_item.title = "Deactivate" if on else "Activate"
        for mode, item in self.mode_items.items():
            item.state = mode == self.selected_mode
        for mins, item in self.duration_items.items():
            item.state = mins == self.selected_minutes

    def _select_duration(self, minutes):
        self.selected_minutes = minutes
        self._refresh()

    # --- callbacks --------------------------------------------------------
    def on_toggle(self, _item):
        if self.mode:
            self.deactivate()
        else:
            self.activate(self.selected_minutes)

    def _make_mode_cb(self, mode):
        def cb(_item):
            self.selected_mode = mode
            if self.mode:
                self.activate(self.selected_minutes, mode)
            else:
                self._refresh()
        return cb

    def _make_duration_cb(self, minutes):
        def cb(_item):
            self.selected_minutes = minutes
            if self.mode:
                self.activate(minutes)
            else:
                self._refresh()
        return cb

    def on_tick(self, _timer):
        # caffeinate died on us (killed externally)? the cup must not lie.
        if self.proc is not None and self.proc.poll() is not None:
            log.warning("caffeinate exited unexpectedly; cup emptied")
            self.deactivate()
            return
        if self.lid_on:
            pct, on_ac = battery()
            if pct is not None and not on_ac and pct <= LOW_BATTERY_PERCENT:
                rumps.notification("slow-coffee", "Battery at %d%%" % pct,
                                   "Lid-closed mode released so the Mac can sleep safely.")
                self.deactivate()

    def on_toggle_login(self, item):
        if exists(LAUNCH_AGENT):
            uninstall_launch_agent()
        else:
            install_launch_agent(start_active=self.settings["coffee_at_boot"],
                                 load=False)
        item.state = exists(LAUNCH_AGENT)

    def on_toggle_coffee_login(self, item):
        item.state = not item.state
        self.settings["coffee_at_boot"] = bool(item.state)
        settings.save(self.settings)
        if exists(LAUNCH_AGENT):
            install_launch_agent(start_active=bool(item.state), load=False)

    def on_about(self, _item):
        rumps.alert("slow-coffee %s" % __version__,
                    "A take-your-time keep-awake indicator.\n"
                    "https://github.com/PLNech/slow-coffee")

    def on_quit(self, _item):
        self.deactivate()
        rumps.quit_application()

    def _on_signal(self, _signum, _frame):
        self.deactivate()
        rumps.quit_application()


# --- login item (LaunchAgent) -------------------------------------------------
def install_launch_agent(start_active=True, load=True):
    """Write the LaunchAgent so slow-coffee starts at login (and optionally now)."""
    args = [sys.executable, "-m", "slow_coffee"]
    if not start_active:
        args.append("--start-inactive")
    plist = {
        "Label": LAUNCH_LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        # Relaunch on a crash, but let Quit (exit 0) stay quit.
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": LOG_PATH,
        "StandardErrorPath": LOG_PATH,
    }
    os.makedirs(dirname(LAUNCH_AGENT), exist_ok=True)
    with open(LAUNCH_AGENT, "wb") as f:
        plistlib.dump(plist, f)
    if load:
        domain = "gui/%d" % os.getuid()
        subprocess.run(["launchctl", "bootout", domain, LAUNCH_AGENT],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "bootstrap", domain, LAUNCH_AGENT], check=False)
    return [LAUNCH_AGENT]


def uninstall_launch_agent():
    removed = []
    domain = "gui/%d" % os.getuid()
    subprocess.run(["launchctl", "bootout", domain, LAUNCH_AGENT],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if exists(LAUNCH_AGENT):
        os.remove(LAUNCH_AGENT)
        removed.append(LAUNCH_AGENT)
    return removed


def run(start_active=True, minutes=0):
    """Run the menu-bar app (blocks)."""
    logging.basicConfig(level=logging.INFO,
                        format="slow-coffee: %(levelname)s %(message)s")
    # Menu-bar only: no Dock icon, no app-switcher entry.
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory)
    SlowCoffeeMac(start_active=start_active, minutes=minutes).run()
