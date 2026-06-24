# slow-coffee - source install helper.
#
#   make install                 # system-wide (uses sudo prefix /usr/local)
#   make install PREFIX=/usr     # for distro packaging (with DESTDIR)
#   make uninstall
#   make icons                   # regenerate the cup PNGs (needs Pillow)
#   make screenshot              # re-render docs/menu.png (needs PyGObject)
#
# The Python module + `slow-coffee` entry point are installed with pip; the
# desktop entry and menu icon are installed as data files so the app shows up
# in your applications menu.

PREFIX  ?= /usr/local
DESTDIR ?=
PYTHON  ?= python3

APPS    = $(DESTDIR)$(PREFIX)/share/applications
ICONS   = $(DESTDIR)$(PREFIX)/share/icons/hicolor/128x128/apps

.PHONY: install uninstall icons screenshot clean

install:
	$(PYTHON) -m pip install --prefix="$(PREFIX)" --root="$(DESTDIR)/" .
	install -Dm644 data/slow-coffee.desktop $(APPS)/slow-coffee.desktop
	install -Dm644 data/icons/slow-coffee.png $(ICONS)/slow-coffee.png
	@echo "Installed. Look for \"Slow Coffee\" in your applications menu."

uninstall:
	-$(PYTHON) -m pip uninstall -y slow-coffee
	rm -f $(APPS)/slow-coffee.desktop $(ICONS)/slow-coffee.png

icons:
	$(PYTHON) make_icons.py
	cp slow_coffee/icons/slow-coffee-full.png data/icons/slow-coffee.png

screenshot:
	/usr/bin/python3 docs/render_menu.py docs/menu.png

clean:
	rm -rf build dist *.egg-info slow_coffee/__pycache__ */__pycache__
