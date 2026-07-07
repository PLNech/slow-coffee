# Changelog

All notable changes to slow-coffee are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/).

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
