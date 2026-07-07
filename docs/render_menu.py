#!/usr/bin/python3
"""Render the slow-coffee menu to a PNG for the README (deterministic, headless).

Uses GTK offscreen rendering so it works without a visible display server stack
and never fights window stacking. The labels and the cup icon are the real ones
shipped in the package, so the image stays faithful to the actual UI.

Run:  /usr/bin/python3 docs/render_menu.py docs/menu.png
"""
import sys
from os.path import join, dirname, abspath

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib  # noqa: E402

from slow_coffee.indicator import DURATIONS, find_icons_dir  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "docs/menu.png"

CSS = b"""
.sc-menu { background: #2b2b2b; border-radius: 12px; padding: 8px;
           border: 1px solid #3d3d3d; }
.sc-menu label { color: #ededed; font-family: "Cantarell", sans-serif; }
.sc-title { font-weight: bold; font-size: 13pt; }
.sc-sub { color: #b08d57; font-size: 9pt; }
.sc-row { font-size: 11pt; padding: 4px 8px; }
.sc-row-on { color: #f4edE0; }
.sc-dim { color: #9a9a9a; }
.sc-sep { background: #3d3d3d; min-height: 1px; margin: 5px 2px; }
"""


def css():
    p = Gtk.CssProvider()
    p.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), p, Gtk.STYLE_PROVIDER_PRIORITY_USER)


def lbl(text, *classes, xalign=0.0):
    w = Gtk.Label(label=text)
    w.set_xalign(xalign)
    for c in classes:
        w.get_style_context().add_class(c)
    return w


def sep():
    s = Gtk.Box()
    s.get_style_context().add_class("sc-sep")
    return s


def build():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.get_style_context().add_class("sc-menu")
    box.set_size_request(250, -1)

    icon = join(find_icons_dir(), "slow-coffee-full.png")
    pix = GdkPixbuf.Pixbuf.new_from_file_at_size(icon, 30, 30)
    head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    head.set_margin_bottom(4)
    head.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 4)
    titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    titles.pack_start(lbl("slow-coffee", "sc-title"), False, False, 0)
    titles.pack_start(lbl("awake · via gnome", "sc-sub"), False, False, 0)
    head.pack_start(titles, False, False, 0)
    box.pack_start(head, False, False, 0)

    box.pack_start(sep(), False, False, 0)
    box.pack_start(lbl("Deactivate", "sc-row"), False, False, 0)
    box.pack_start(sep(), False, False, 0)

    for i, (label, _m) in enumerate(DURATIONS):
        bullet = "◉ " if i == 0 else "○ "
        cls = "sc-row-on" if i == 0 else "sc-dim"
        box.pack_start(lbl(bullet + label, "sc-row", cls), False, False, 0)

    box.pack_start(sep(), False, False, 0)
    box.pack_start(lbl("☑ Start at boot", "sc-row", "sc-row-on"), False, False, 0)
    box.pack_start(lbl("☑ Coffee at boot", "sc-row", "sc-row-on"), False, False, 0)

    box.pack_start(sep(), False, False, 0)
    box.pack_start(lbl("About slow-coffee", "sc-row", "sc-dim"), False, False, 0)
    box.pack_start(lbl("Quit", "sc-row", "sc-dim"), False, False, 0)
    return box


def main():
    css()
    win = Gtk.OffscreenWindow()
    win.add(build())
    win.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()
    surf = win.get_surface()
    surf.write_to_png(OUT)
    print("wrote", OUT, surf.get_width(), "x", surf.get_height())


if __name__ == "__main__":
    main()
