"""Command-line entry point for slow-coffee."""

import sys
import signal
import argparse

from . import __version__


def _run_indicator(start_active, minutes):
    # Import the indicator first: it pins gi.require_version("Gtk", "3.0") before
    # anything pulls in gi.repository.Gtk (which would otherwise default to 4.0).
    # Kept lazy so --install-desktop / --version work without a display.
    from .indicator import SlowCoffee
    from gi.repository import Gtk, GLib

    app = SlowCoffee(start_active=start_active, minutes=minutes)

    def handler(*_a):
        app.deactivate()
        Gtk.main_quit()
        return False

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, handler)

    Gtk.main()


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="slow-coffee",
        description="A take-your-time keep-awake tray indicator (on by default).")
    p.add_argument("-V", "--version", action="version",
                   version="%(prog)s " + __version__)
    p.add_argument("--start-inactive", action="store_true",
                   help="boot decaf (cup empty); default is on")
    p.add_argument("--minutes", type=int, default=0, metavar="N",
                   help="stay awake N minutes then auto-off (0 = forever)")
    p.add_argument("--install-desktop", action="store_true",
                   help="add the launcher + icon to your app menu (no root)")
    p.add_argument("--autostart", action="store_true",
                   help="with --install-desktop, also start at login")
    p.add_argument("--uninstall-desktop", action="store_true",
                   help="remove the launcher/icon/autostart entries")
    args = p.parse_args(argv)

    if args.uninstall_desktop:
        from . import desktop
        removed = desktop.uninstall()
        print("Removed:" if removed else "Nothing to remove.")
        for path in removed:
            print("  -", path)
        return 0

    if args.install_desktop:
        from . import desktop
        written = desktop.install(autostart=args.autostart)
        print("Installed:")
        for path in written:
            print("  +", path)
        print("\nLook for \"Slow Coffee\" in your applications menu.")
        return 0

    _run_indicator(start_active=not args.start_inactive, minutes=args.minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
