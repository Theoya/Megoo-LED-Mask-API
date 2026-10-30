"""Creature: an original six-eyed thing that uses the visor's eye holes as its two main eyes.

Design (own, not based on any existing character): amber rims glow around the two real eye
holes under hard, slanted brow ridges; four small extra eyes (two on the forehead, two on the
cheeks) sit closed until they open one by one; below, a long jagged grin of interlocking
cyan teeth slowly widens, parts to show a red throat glow, closes again, then the creature
blinks every eye at once and the extra eyes shut in turn. No feature is drawn on the eye-hole
cells; the main eyes are the holes themselves, outlined by their glowing rims.

Each frame is a keyframe (no motion blur: the expression changes are deliberate steps).

  python experiments/creature.py [--out images/experiments/creature]
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import EYE, H, W, eye_dist, grid, save  # noqa: E402

SS = 4
AMBER = (255, 130, 0)
TOOTH = (0, 230, 255)
BROW = (0, 190, 255)
THROAT = (200, 0, 20)
EXTRA = [(16.5, 6.5), (29.5, 6.5), (6.5, 29.5), (39.5, 29.5)]  # small extra eyes (x, y)

# per frame: main eyes open (0..1), extra eyes open x4, grin half-width, corner lift, mouth open
F = [
    dict(main=1.0, extra=(0, 0, 0, 0), w=14.0, lift=2.5, open=0.0),
    dict(main=1.0, extra=(0.5, 0, 0, 0), w=14.0, lift=2.5, open=0.0),
    dict(main=1.0, extra=(1, 0.5, 0, 0), w=14.5, lift=2.8, open=0.0),
    dict(main=1.0, extra=(1, 1, 0.5, 0.2), w=15.0, lift=3.1, open=0.0),
    dict(main=1.0, extra=(1, 1, 1, 0.7), w=15.8, lift=3.5, open=1.0),
    dict(main=1.0, extra=(1, 1, 1, 1), w=16.5, lift=4.0, open=3.0),
    dict(main=1.0, extra=(1, 1, 1, 1), w=17.2, lift=4.6, open=5.2),
    dict(main=1.0, extra=(1, 1, 1, 1), w=17.6, lift=5.0, open=7.0),
    dict(main=1.0, extra=(1, 1, 1, 1), w=17.6, lift=5.0, open=6.0),
    dict(main=1.0, extra=(1, 1, 1, 1), w=17.0, lift=4.5, open=2.8),
    dict(main=0.0, extra=(0, 0, 0, 0), w=16.5, lift=4.0, open=0.5),
    dict(main=1.0, extra=(1, 1, 1, 1), w=16.0, lift=3.6, open=0.0),
    dict(main=1.0, extra=(0.4, 1, 1, 0.3), w=15.2, lift=3.1, open=0.0),
    dict(main=1.0, extra=(0, 0.3, 0.4, 0), w=14.5, lift=2.7, open=0.0),
]


def P(x, y):
    return (x * SS, y * SS)


def draw_frame(k, rim_d, i):
    img = Image.new("RGB", (W * SS, H * SS))
    d = ImageDraw.Draw(img)
    # brow ridges: hard, slanting down toward the nose, clear of the eye holes
    d.line([P(3.5, 9.5), P(10, 10.5), P(17.5, 12.6)], fill=BROW, width=SS)
    d.line([P(42.5, 9.5), P(36, 10.5), P(28.5, 12.6)], fill=BROW, width=SS)
    # extra eyes: amber almonds with a dark slit pupil; closed = a dim lid line
    for (ex, ey), o in zip(EXTRA, k["extra"]):
        if o <= 0.05:
            d.line([P(ex - 2.2, ey), P(ex + 2.2, ey)], fill=(110, 40, 0), width=SS)
            continue
        hh = 0.5 + 1.1 * o
        d.ellipse([P(ex - 2.4, ey - hh), P(ex + 2.4, ey + hh)], fill=AMBER)
        if o > 0.6:
            d.rectangle([P(ex - 0.5, ey - hh + 0.4), P(ex + 0.5, ey + hh - 0.4)], fill=(30, 0, 0))
    # the grin
    cx, cy, w, lift, op = 23.0, 42.0, k["w"], k["lift"], k["open"]
    xs = np.linspace(cx - w, cx + w, 61)
    u = (xs - cx) / w
    top = cy - lift * u ** 2
    bot = top + op * np.clip(1 - u ** 2, 0, None) ** 0.8
    if op > 0.2:
        d.polygon([P(x, y) for x, y in zip(xs, top)] + [P(x, y) for x, y in zip(xs[::-1], bot[::-1])],
                  fill=(int(THROAT[0] * min(1, 0.3 + op / 5)), 0, 0))
    # interlocking teeth: zig-zag between the lips; with the mouth open they part into fangs
    step = 2.9
    tx = np.arange(cx - w + 1.6, cx + w - 1.4, step)
    for j, x in enumerate(tx):
        uu = (x - cx) / w
        t_y = cy - lift * uu ** 2
        b_y = t_y + op * max(0.0, 1 - uu ** 2) ** 0.8
        ln = (1.9 + 0.3 * op) * (0.55 + 0.45 * max(0.0, 1 - uu ** 2))
        if j % 2 == 0:  # upper fang
            d.polygon([P(x - 1.2, t_y - 0.4), P(x + 1.2, t_y - 0.4), P(x, t_y + ln)], fill=TOOTH)
        else:  # lower fang
            d.polygon([P(x - 1.2, b_y + 0.4), P(x + 1.2, b_y + 0.4), P(x, b_y - ln)], fill=TOOTH)
    d.line([P(x, y - 0.3) for x, y in zip(xs, top)], fill=TOOTH, width=max(1, SS // 2))
    if op > 0.2:
        d.line([P(x, y + 0.3) for x, y in zip(xs, bot)], fill=TOOTH, width=max(1, SS // 2))
    # mouth corners curl into short creases
    for sgn in (-1, 1):
        x0, y0 = cx + sgn * w, cy - lift
        d.line([P(x0, y0), P(x0 + sgn * 1.6, y0 - 1.8)], fill=TOOTH, width=SS // 2)

    a = np.asarray(img, float)
    bloom = np.asarray(img.filter(ImageFilter.GaussianBlur(SS * 1.2)), float)
    dark = (a.max(-1, keepdims=True) < 40)  # bloom only onto unlit areas, so colours don't mix to pastel
    a = np.where(dark, np.maximum(a, bloom * 0.6), a)
    small = a.reshape(H, SS, W, SS, 3).mean(axis=(1, 3))
    # main eyes: the holes themselves, outlined by a glowing amber rim; shut = a low lid crease
    breathe = 0.85 + 0.15 * np.cos(2 * np.pi * i / len(F))
    if k["main"] > 0.5:
        rim = np.exp(-np.clip(rim_d - 1.0, 0, None) / 0.9) * breathe
        small = np.maximum(small, rim[..., None] * np.array(AMBER))
    else:
        xs1, ys1 = grid(1)
        lid = np.zeros((H, W))
        for (lx0, lx1, ly) in [(6, 17, 23.5), (29, 40, 21.5)]:
            m = (xs1 > lx0) & (xs1 < lx1)
            yy = ly - 1.6 * np.abs((xs1 - (lx0 + lx1) / 2) / ((lx1 - lx0) / 2)) ** 2
            lid = np.maximum(lid, m * np.exp(-((ys1 - yy) / 0.6) ** 2))
        lid *= ~EYE
        small = np.maximum(small, lid[..., None] * np.array([200, 90, 0]))
    return small


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="images/experiments/creature")
    a = ap.parse_args()
    xs, ys = grid(1)
    rim_d = eye_dist(xs, ys)
    save([draw_frame(k, rim_d, i) for i, k in enumerate(F)], a.out, cols=7)


if __name__ == "__main__":
    main()
