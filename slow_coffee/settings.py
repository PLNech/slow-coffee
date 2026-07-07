"""Tiny persisted preferences for the tray's boot toggles.

Only one thing needs storing: ``coffee_at_boot`` - whether an autostart launch
boots caffeinated (cup full) or decaf (``--start-inactive``). The *other* toggle,
"start at boot", has a better source of truth than a settings key: the presence
of the autostart ``.desktop`` file itself (see ``desktop.autostart_enabled``), so
it's never stored here and never drifts from reality.

Stored as JSON under ``$XDG_CONFIG_HOME`` (``~/.config/slow-coffee/settings.json``).
Written atomically. Missing/corrupt file falls back to the defaults.
"""

import json
import os
from os.path import join, dirname, exists

DEFAULTS = {"coffee_at_boot": True}


def path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return join(base, "slow-coffee", "settings.json")


def load(seed=None):
    """Return the settings dict (defaults merged with whatever is on disk).

    ``seed`` supplies fallbacks for keys absent from BOTH the file and the
    defaults' caller intent - used on first run to inherit the state of a
    pre-existing autostart entry rather than clobbering it with a bare default.
    """
    data = {}
    try:
        with open(path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    merged = dict(DEFAULTS)
    if seed:
        merged.update({k: bool(seed[k]) for k in DEFAULTS if k in seed})
    merged.update({k: bool(data[k]) for k in DEFAULTS if k in data})
    return merged


def save(settings):
    """Persist the given settings (only known keys), atomically. Returns the merged dict."""
    p = path()
    os.makedirs(dirname(p), exist_ok=True)
    merged = {k: bool(settings.get(k, DEFAULTS[k])) for k in DEFAULTS}
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(merged, f, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return merged
