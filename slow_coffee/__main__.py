"""Command-line entry point for slow-coffee."""

import sys
import signal
import logging
import argparse

from . import __version__


def _run_indicator(start_active, minutes):
    if sys.platform == "darwin":
        from . import mac
        mac.run(start_active=start_active, minutes=minutes)
        return

    # Import the indicator first: it pins gi.require_version("Gtk", "3.0") before
    # anything pulls in gi.repository.Gtk (which would otherwise default to 4.0).
    # Kept lazy so --install-desktop / --version work without a display.
    from .indicator import SlowCoffee
    from gi.repository import Gtk, GLib

    # One line on stderr per notable event; under an autostart/systemd launch
    # that lands in the journal, which is where you'd look anyway.
    logging.basicConfig(level=logging.INFO,
                        format="slow-coffee: %(levelname)s %(message)s")

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

    if args.uninstall_desktop and sys.platform == "darwin":
        from . import mac
        removed = mac.uninstall_launch_agent()
        print("Removed:" if removed else "Nothing to remove.")
        for path in removed:
            print("  -", path)
        return 0

    if args.install_desktop and sys.platform == "darwin":
        # macOS has no app-menu .desktop; the login item IS the install. It also
        # starts the menu-bar app right away, so the cup appears without a re-login.
        from . import mac
        for path in mac.install_launch_agent(start_active=not args.start_inactive):
            print("  +", path)
        print("\nslow-coffee is running in your menu bar and will start at login.")
        return 0

    if args.uninstall_desktop:
        from . import desktop
        removed = desktop.uninstall()
        print("Removed:" if removed else "Nothing to remove.")
        for path in removed:
            print("  -", path)
        return 0

    if args.install_desktop:
        from . import desktop
        written = desktop.install(autostart=args.autostart,
                                  start_active=not args.start_inactive)
        print("Installed:")
        for path in written:
            print("  +", path)
        print("\nLook for \"Slow Coffee\" in your applications menu.")
        return 0

    _run_indicator(start_active=not args.start_inactive, minutes=args.minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
