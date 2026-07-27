# Changelog

All notable changes to slow-coffee are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

## [Unreleased]

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
