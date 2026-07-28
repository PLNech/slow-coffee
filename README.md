<div align="center">

# ☕ slow-coffee

**A take-your-time keep-awake indicator for your system tray. On by default.**

A small coffee cup lives in your top bar and stops the screen blanking or the
session going idle. The cup boots *full*, because the whole point is to stay
awake until you say otherwise. No daemon to babysit, no battery to drain - it
just holds a D-Bus idle inhibitor and gets out of the way.

[![CI](https://github.com/PLNech/slow-coffee/actions/workflows/ci.yml/badge.svg)](https://github.com/PLNech/slow-coffee/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/slow-coffee.svg)](https://pypi.org/project/slow-coffee/)
[![AUR](https://img.shields.io/aur/version/slow-coffee.svg)](https://aur.archlinux.org/packages/slow-coffee)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<img src="docs/menu.png" alt="slow-coffee tray menu" width="260">

</div>

## Why another caffeine?

Most keep-awake tools assume you want a *trigger*: stay awake while a video
plays, while music plays, while an app is fullscreen. slow-coffee assumes the
opposite. You opened it because you want to stay awake **now**, so it starts
caffeinated and stays that way. One click (or a timer) to stop.

- **On by default.** Cup full at launch. `--start-inactive` if you disagree.
- **Honest icon.** Full = really inhibiting; empty = really not. No lying cup.
- **Idle-only by default.** The screen stays on, but closing the lid still
  suspends - safer for a laptop than blocking all sleep.
- **Sleep mode, opt-in.** Menu radio group: **Idle only** / **Sleep only**
  (lid-close safe, screen can still lock) / **Both**. For a detached job you
  can't afford to lose to a suspend.
- **Timers.** 30 min / 1 hour / 2 hours / indefinitely, from the menu or
  `--minutes N`.
- **Slow mode, if your host can.** On machines with a thermal-mode helper,
  closing the lid while caffeinated drops the CPU to its quietest mode (and puts
  the old one back on open), with a temperature watchdog that lets the machine
  sleep if it starts cooking anyway. Invisible everywhere else.
- **Quiet while caffeinated, opt-in.** Same helper, coupled to the coffee rather
  than the lid: stay awake *and* stay silent for as long as the cup is full.
- **Self-contained.** Bundles its own icons; no theme or extra data package.

## Install

slow-coffee needs the GObject introspection runtime (GTK 3 + Ayatana
AppIndicator). On a tray that hides AppIndicators (e.g. vanilla GNOME), enable
the *AppIndicator support* extension.

### Arch (AUR)

```sh
yay -S slow-coffee          # or: paru -S slow-coffee
```

### Debian / Ubuntu (.deb)

Grab `slow-coffee_*.deb` from the [latest release](https://github.com/PLNech/slow-coffee/releases) and:

```sh
sudo apt install ./slow-coffee_1.0.0_all.deb
```

### pip (PyPI)

The GIR libraries come from your distro, the rest from pip:

```sh
# Debian/Ubuntu
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
# Fedora
sudo dnf install python3-gobject gtk3 libayatana-appindicator-gtk3

pip install --user slow-coffee
slow-coffee --install-desktop      # add it to your applications menu (no root)
```

### From source

```sh
git clone https://github.com/PLNech/slow-coffee
cd slow-coffee
sudo make install                  # PREFIX=/usr/local by default
```

## Usage

Run `slow-coffee` (or launch **Slow Coffee** from your applications menu). The
cup appears in the tray, already caffeinated. Click it for the menu:

| Item | Does |
|------|------|
| Activate / Deactivate | Toggle the inhibitor by hand |
| Indefinitely / 30 min / 1 h / 2 h | Pick a duration; it auto-stops when time is up |
| Start at boot | Add/remove the autostart entry - launch slow-coffee at login |
| Coffee at boot | When autostarted, boot caffeinated (off = boot decaf) |
| Quiet while caffeinated | *(only where available)* Off by default; keep the machine in its quiet thermal mode for as long as the coffee is on |
| Slow when lid closed | *(only where available)* Go slow automatically on lid-close |
| Slow now | *(only where available)* Engage/release slow mode by hand |
| About / Quit | The usual |

**Start at boot** and **Coffee at boot** are the checkbox equivalents of
`--autostart` and `--start-inactive`: the first writes (or removes) the
autostart `.desktop` entry, the second decides whether that entry launches
caffeinated. They configure the *next* login, not the running cup, and the
"coffee at boot" choice is remembered in `~/.config/slow-coffee/settings.json`.

```sh
slow-coffee                  # start caffeinated, forever
slow-coffee --minutes 90     # caffeinated for 90 minutes, then off
slow-coffee --start-inactive # start decaf; toggle on from the tray
slow-coffee --install-desktop --autostart   # menu entry + start at login
slow-coffee --uninstall-desktop             # undo the above
```

## How it works

slow-coffee asks the session to not go idle, preferring whatever is actually
running and falling back gracefully:

1. **`org.gnome.SessionManager.Inhibit`** (flags: idle `8`, suspend `4`,
   both `12`) - GNOME.
   *X11 verified; GNOME-Wayland expected but untested by the author.*
2. **`org.freedesktop.ScreenSaver.Inhibit`** - KDE and other freedesktop shells.
3. **`systemd-inhibit --what=...`** - logind fallback for bare window managers.

In the *Idle only* mode (the default) lid-close still sleeps the machine, by
design. In *Sleep only* / *Both*, slow-coffee additionally spawns a
`systemd-inhibit --what=sleep:handle-lid-switch` companion process on backends
1 and 2 - session-level suspend inhibitors are *not* lid-close inhibitors,
since logind's default `LidSwitchIgnoreInhibited=yes` ignores everything that
doesn't name `handle-lid-switch`. Backend 3 already names it itself.

It holds one persistent session-bus connection and releases every inhibitor on
quit. Wayland note: the idle-inhibit protocol is per-surface, so a global manual
inhibitor is shell-dependent; under GNOME the SessionManager call covers it,
under wlroots your mileage varies and the `systemd-inhibit` fallback handles
sleep.

## Slow mode (optional)

A laptop kept awake with the lid shut is a laptop running in a closed box. If
your host provides a thermal-mode helper, slow-coffee can turn that into a
non-problem:

- **Requires** an executable `/usr/local/sbin/thermal-mode-apply <mode>`
  (callable via `sudo -n`, i.e. whitelisted in sudoers) and a readable
  `/etc/thermal-mode.conf` holding `MODE=<mode>`. If either is missing, the three
  menu items never appear and slow-coffee behaves exactly as before - nothing to
  opt out of. The helper is a *separate* tool, not shipped here: the author's
  lives in a private SRE repo under `thermal/`, where it steers CPU watts and, on
  Dell hardware, optionally pushes a BIOS thermal profile. Anything that accepts a
  mode name and records it in that file will do.
- **Quiet while caffeinated** (*off* by default) couples the thermal mode to the
  coffee instead of the lid: some rooms make fan noise the thing you can't
  afford - a recording, a talk, a sleeping flat - so while the coffee is on the
  machine sits in `silent`, and the mode you had comes back when it goes off.
  Trading watts for silence is a real choice, so this one waits to be asked.
  It layers *under* slow mode rather than fighting it: quiet remembers the mode
  you picked, slow remembers the one quiet left, and each only ever restores what
  it found - so a lid-close while quiet still deepens to `slow`, and opening it
  returns to `silent` until the coffee stops.
- **On lid close** (while caffeinated, with *Slow when lid closed* on) it
  snapshots the current mode and asks the helper for `slow` (a sealed-space
  power budget), falling back to `silent` where the helper doesn't know it.
  **On lid open** - or when the coffee goes off, or on quit - it puts the
  snapshot back. If you changed modes yourself in the meantime, your choice is
  left alone.
- Lid state comes from logind's `LidClosed` property over D-Bus (a signal
  subscription, not a poll), debounced by 2 s - some lid switches bounce.
- **Thermal watchdog, two thermometers.** While slow, temperatures are sampled
  every 30 s: the CPU package (`x86_pkg_temp` zone, found by name - zone
  numbering is firmware order - or the `coretemp` hwmon) against a 90 °C limit,
  and the chassis-skin zone (type `TSKN`, where the host has one) against
  68 °C. The die catches a runaway CPU; the skin catches slow heat-soak in a
  closed bag, which a power-capped die never reports. Three consecutive strikes
  on either and slow-coffee releases every inhibitor, notifies you, and forces
  the suspend with `systemctl suspend -i` - polite suspends can be vetoed by
  other apps' inhibitors, and letting the machine sleep beats cooking it. The
  app stays running, decaf.
- **Telemetry.** Every watchdog sample lands in
  `~/.local/state/slow-coffee/thermal.csv` (ISO time, die °C, skin °C), so if
  something ever runs hot you have the receipts.
- If a discrete GPU is awake (`runtime_status` not `suspended`) when slow mode
  engages, you get a heads-up notification. slow-coffee only ever *reads* that
  file.

## Prior art

This is an independent implementation, written from scratch, but it stands on
the shoulders of two lovely projects worth knowing:

- [caffeine](https://launchpad.net/caffeine) by Reuben Thomas - the classic
  fullscreen-auto daemon + manual indicator (GPL-3, X11).
- [caffeine-ng](https://codeberg.org/WhyNotHugo/caffeine-ng) by Hugo Barrera -
  the modern rewrite with presentation-mode and audio awareness.

slow-coffee differs by being *on by default*, using D-Bus inhibition (rather
than an unmapped X window), shipping as a single self-contained package, and
living under MIT.

## Development

```sh
python3 -m slow_coffee            # run from a checkout (needs system PyGObject)
python3 make_icons.py             # regenerate the cup icons (needs Pillow)
/usr/bin/python3 docs/render_menu.py docs/menu.png   # re-render the screenshot
```

## License

[MIT](LICENSE) (c) 2026 PLNech.
