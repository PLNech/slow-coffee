#!/usr/bin/env python3
"""Generate the slow-coffee tray icons.

Two states, drawn 4x then downscaled for clean anti-aliasing:
  - slow-coffee-full.png  : solid cup + rising steam   (awake / caffeinated)
  - slow-coffee-empty.png  : cup outline, no steam     (off / decaf)

Monochrome warm-cream so the silhouette reads on a dark GNOME top bar; the
full/empty distinction is shape (filled vs outline), not colour, so state stays
legible whatever the panel theme. Run:  python3 make_icons.py
"""
import math
from PIL import Image, ImageDraw

S = 512                      # supersample canvas; final icons are 128px
CREAM = (244, 237, 224, 255)
STROKE = max(2, S // 28)
OUT = "slow_coffee/icons"


def cup_polygon():
    # slightly tapered body, in 0..512 space
    top_l, top_r, top_y = 0.27 * S, 0.66 * S, 0.40 * S
    bot_l, bot_r, bot_y = 0.33 * S, 0.60 * S, 0.74 * S
    return [(top_l, top_y), (top_r, top_y), (bot_r, bot_y), (bot_l, bot_y)], top_y, top_r


def draw_handle(d, top_r, outline_only):
    # an open C handle hugging the right wall of the cup
    box = [top_r - 0.10 * S, 0.46 * S, top_r + 0.14 * S, 0.70 * S]
    d.arc(box, start=-78, end=78, fill=CREAM, width=STROKE)


def draw_steam(d):
    for cx in (0.37 * S, 0.46 * S, 0.55 * S):
        pts = []
        for i in range(0, 41):
            t = i / 40.0
            y = (0.36 * S) - t * (0.26 * S)            # rise upward
            x = cx + math.sin(t * math.pi * 2.2) * (0.035 * S)
            pts.append((x, y))
        d.line(pts, fill=CREAM, width=max(2, STROKE - 4), joint="curve")


def render(full):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    body, top_y, top_r = cup_polygon()

    if full:
        d.polygon(body, fill=CREAM)
        # coffee rim ellipse to give the silhouette a "cup mouth"
        d.ellipse([body[0][0], top_y - 0.05 * S, body[1][0], top_y + 0.05 * S],
                  fill=CREAM)
        draw_handle(d, top_r, outline_only=False)
        draw_steam(d)
    else:
        d.line(body + [body[0]], fill=CREAM, width=STROKE, joint="curve")
        d.ellipse([body[0][0], top_y - 0.05 * S, body[1][0], top_y + 0.05 * S],
                  outline=CREAM, width=STROKE)
        draw_handle(d, top_r, outline_only=True)

    return img.resize((128, 128), Image.LANCZOS)


if __name__ == "__main__":
    import os
    os.makedirs(OUT, exist_ok=True)
    render(True).save(os.path.join(OUT, "slow-coffee-full.png"))
    render(False).save(os.path.join(OUT, "slow-coffee-empty.png"))
    print("wrote", OUT + "/slow-coffee-{full,empty}.png")
