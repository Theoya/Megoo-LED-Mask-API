"""Watchers: the eye holes glow from inside, as if something behind the visor is looking out.

Each eye gets a halo measured from the eye-hole cells, plus a hot "pupil" light sitting inside
the hole near its edge. The pupil shifts left and right (the glow piles up on the side it looks
toward), holds, flares once, then sinks to a dim smoulder. Thin smoke curls up off the brow
from both eyes. Keyframes are interpolated around the loop and each frame bakes in 4
sub-frames of motion blur, so the hard 1 s cuts read as a glance rather than a jump.

  python experiments/watchers.py [--out images/experiments/watchers --frames 16 --palette acid|ember|violet]
"""
import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import EYES, blur_frames, down, eye_dist, grid, rho, save, smooth  # noqa: E402

SS = 2
PALETTES = {  # (value, rgb) stops, dark -> hot; all saturated
    "acid": [(0, (0, 0, 0)), (0.2, (0, 45, 0)), (0.5, (20, 170, 0)), (0.8, (110, 255, 0)), (1, (220, 255, 0))],
    "ember": [(0, (0, 0, 0)), (0.2, (70, 0, 0)), (0.5, (220, 10, 0)), (0.8, (255, 90, 0)), (1, (255, 190, 0))],
    "violet": [(0, (0, 0, 0)), (0.2, (35, 0, 70)), (0.5, (130, 0, 220)), (0.8, (255, 0, 200)), (1, (255, 60, 120))],
}
# keyframes: (beat, gaze dx in LEDs, strength, halo size). Beats are 0..16 around the loop.
KEYS = [(0, 0, 0.45, 2.2), (1.5, 0, 0.55, 2.4), (3, -2.8, 0.75, 2.6), (5, -2.8, 0.75, 2.6), (6.5, 0, 0.5, 2.3),
        (8, 2.8, 0.75, 2.6), (10, 2.8, 0.8, 2.7), (11.5, 0, 0.6, 2.5), (12.5, 0, 1.0, 4.2), (13.5, 0, 0.85, 3.4),
        (14.5, 0, 0.25, 1.8), (16, 0, 0.45, 2.2)]


def keyed(beat):
    for (b0, *v0), (b1, *v1) in zip(KEYS, KEYS[1:]):
        if b0 <= beat <= b1:
            f = (beat - b0) / (b1 - b0)
            f = f * f * (3 - 2 * f)
            return [x0 + (x1 - x0) * f for x0, x1 in zip(v0, v1)]
    return KEYS[0][1:]


def palette(v, stops):
    v = np.clip(v, 0, 1)
    out = np.zeros(v.shape + (3,))
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        m = (v >= a) & (v <= b)
        f = ((v - a) / (b - a))[m][:, None]
        out[m] = np.array(ca) * (1 - f) + np.array(cb) * f
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="images/experiments/watchers")
    ap.add_argument("--frames", type=int, default=16)
    ap.add_argument("--palette", choices=sorted(PALETTES), default="acid")
    a = ap.parse_args()
    stops = PALETTES[a.palette]

    xs, ys = grid(SS)
    r = rho(xs, ys)
    rim = eye_dist(xs, ys)
    D = 24.0
    smoke_w = [(0.5, 2, 0.9, 0.3), (0.35, 3, -1.3, 2.0), (0.25, 5, 0.6, 4.1)]

    def render(t):
        dx, k, size = keyed(16 * t)
        glow = np.zeros(xs.shape)
        for e in EYES:
            ex, ey = e["center"]
            # pupil: a hot point light inside the hole, pushed toward the gaze side
            hw = (e["bbox"][2] - e["bbox"][0]) / 2
            px = ex + np.clip(dx * 1.6, -hw + 1.5, hw - 1.5)
            pd = np.hypot((xs - px) / 1.25, ys - ey)
            glow = np.maximum(glow, np.exp(-np.clip(pd - 3.0, 0, None) / (0.8 * size)) * 0.95)
        halo = np.exp(-rim / size) * 0.55
        v = k * np.maximum(glow * smooth(0, 1.5, rim + 0.5), halo)
        # smoke curling up off the brow, fed by the eye glow
        ph = 2 * math.pi * t
        sm = sum(amp * np.sin(kx * xs + 2 * math.pi * m / D * (ys + D * t) + p + 1.2 * np.sin(0.4 * ys + ph))
                 for amp, m, kx, p in smoke_w)
        above = np.zeros(xs.shape)
        for e in EYES:
            ex, ey = e["center"]
            above = np.maximum(above, np.exp(-((xs - ex) / 5.5) ** 2) * smooth(ey - 3, ey - 6, ys) * smooth(2, 9, ys))
        smoke = np.clip(sm, 0, None) * above * (0.35 + 0.55 * k)
        v = np.maximum(v, smoke * 0.75)
        v *= 1 - smooth(0.95, 1.02, r)
        return down(palette(v, stops), SS)

    save(blur_frames(render, a.frames), a.out)


if __name__ == "__main__":
    main()
