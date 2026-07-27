# Changelog

All notable changes to slow-coffee are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

## [Unreleased]

### Changed — lessons from a field overheat (2026-07-27)
A lid-shut, slow-mode laptop still overheated in a backpack: the power-capped
CPU die never neared 90 °C, but half an hour of steady 15 W+ into a sealed bag
soaked the chassis past finger-safe, and the die-only watchdog stayed silent
the whole time. Everything below exists because of that ride.

- **Skin-temperature watchdog**: alongside the 90 °C die check, slow mode now
  watches the chassis-skin thermal zone (type `TSKN`, where the host has one)
  and trips at three consecutive samples above 68 °C. The die catches a runaway
  CPU; the skin catches slow heat-soak the die never reports.
- **The safety valve now forces the suspend** with `systemctl suspend -i`
  after releasing its own inhibitors: any other block inhibitor on the system
  (another caffeine app, a stray `systemd-inhibit`) would veto a polite
  suspend and keep the machine cooking.
- **Thermal telemetry**: while slow is engaged, every watchdog sample is
  appended to `~/.local/state/slow-coffee/thermal.csv` (ISO time, die °C,
  skin °C; rotates past ~1 MB) so the next post-mortem has receipts.
- **Deeper slow mode where available**: slow mode now asks the helper for a
  `slow` mode first (a sealed-space power budget) and falls back to `silent`
  on hosts that don't know it.
- **Lid debounce** (2 s): some lid switches bounce (kernel: "not compliant to
  SW_LID"); a transition is acted on only once it has held.
- **Autostart uses an absolute Exec path**: systemd's xdg-autostart-generator
  resolves `Exec=` before `~/.local/bin` is on PATH, so a bare name from a
  pip --user install logged "Exec binary does not exist" at every boot.

### Added
- **Slow mode** (opt-out, and only where the host supports it): while
  caffeinated, closing the lid switches the machine to its quietest thermal mode
  via `/usr/local/sbin/thermal-mode-apply silent` and restores the previous mode
  on lid-open, coffee-off or quit - unless the mode changed underneath us, in
  which case the user's choice wins. Lid state comes from logind's `LidClosed`
  property over D-Bus. Two new menu items, *Slow when lid closed* (persisted as
  `slow_when_closed`) and *Slow now*; both are absent, along with all of the
  behaviour, when the helper or `/etc/thermal-mode.conf` isn't there.
- **Thermal watchdog** while slow: samples the CPU package temperature every 30 s
  (`x86_pkg_temp` zone discovered by name, `coretemp` hwmon as fallback) and, at
  three consecutive samples above 90 °C, releases every inhibitor so the machine
  can suspend, with a notification explaining why. Also warns once if a discrete
  GPU is awake (read-only) when slow mode engages.
- Going slow now **blanks the display** when the lid is genuinely shut, via
  `org.gnome.ScreenSaver.SetActive`, falling back to `org.freedesktop.ScreenSaver`
  and then to `xset dpms force off` on X11; the backend used is logged. In
  practice GNOME's `SetActive` does not answer while the session is inhibited
  and the freedesktop name rejects `SetActive` outright, so `xset` is what
  carries an X11 GNOME session - the D-Bus attempts are kept short (1.2 s) so a
  wedged screensaver can't stall the lid-close path. The
  idle inhibitor that keeps the machine awake is what stops the desktop blanking
  by itself, so a closed laptop used to sit there with its panel lit. *Slow now*
  with the lid open leaves the screen alone. If no backend will blank, slow mode
  still engages and says so once.
- Notable events are now logged (stderr / journal).

## [1.2.0] - 2026-07-26

### Added
- Inhibit-mode radio group in the tray menu: **Idle only** (default, lid-close
  still suspends) / **Sleep only** (screen can still lock, but the machine
  won't suspend - lid-close included) / **Both**. Sleep coverage on GNOME is
  one `SessionManager.Inhibit` flag; on the freedesktop ScreenSaver / bare-WM
  fallbacks (which have no "suspend" concept of their own) it's a companion
  `systemd-inhibit --what=sleep:handle-lid-switch` process alongside the idle
  leg. Switching mode while active releases and re-acquires with the new flags.

## [1.1.0] - 2026-07-07

### Added
- Two boot toggles in the tray menu: **Start at boot** (writes/removes the
  autostart `.desktop` entry) and **Coffee at boot** (whether an autostarted
  launch boots caffeinated or decaf). The checkbox equivalents of `--autostart`
  and `--start-inactive`.
- The "coffee at boot" choice persists in `~/.config/slow-coffee/settings.json`;
  "start at boot" is read from (and written to) the autostart entry itself, so
  the checkboxes never drift from reality. On first run the "coffee at boot"
  state is seeded from any pre-existing autostart entry.

## [1.0.0] - 2026-06-25

First release.

### Added
- Tray indicator (Ayatana AppIndicator) with a coffee-cup icon: full when
  caffeinated, empty when not.
- **On by default** - the cup boots full; the whole point is to *stay* awake
  until you say otherwise. `--start-inactive` opts out.
- Idle inhibition via a preference chain: `org.gnome.SessionManager` ->
  `org.freedesktop.ScreenSaver` -> `systemd-inhibit` (logind) fallback.
- Idle-only by default (lid-close still suspends; safer for a laptop).
- Timed sessions from the menu: 30 minutes / 1 hour / 2 hours / indefinitely,
  or `--minutes N`.
- `--install-desktop` / `--uninstall-desktop`: add or remove an app-menu entry
  and icon under your XDG dirs, no root required; `--autostart` adds login start.
- Packaging: PyPI (pip), Arch AUR (PKGBUILD), Debian/Ubuntu (.deb).

[1.1.0]: https://github.com/PLNech/slow-coffee/releases/tag/v1.1.0
[1.0.0]: https://github.com/PLNech/slow-coffee/releases/tag/v1.0.0
