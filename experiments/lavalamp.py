"""Lava lamp: slow metaballs that rise, sink, merge and pinch apart inside the oval.

Three-colour palette (default: deep violet glow -> magenta skin -> orange core). Each blob
moves on a closed path with whole-number cycles per loop (seamless) and breathes in size;
blobs near each other fuse because the field is a sum. Motion blur: 4 sub-frames per frame.
The eye holes simply sit in the flow; blobs drift through them without losing any detail.

  python experiments/lavalamp.py [--out images/experiments/lavalamp --frames 20 --scheme magma|toxic|abyss]
"""
import argparse
import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import blur_frames, down, grid, rho, save, smooth  # noqa: E402

SS = 4
SCHEMES = {  # (field value, rgb) stops: glow, skin, core
    "magma": [(0.0, (0, 0, 0)), (0.15, (25, 0, 55)), (0.6, (90, 0, 170)), (1.0, (255, 0, 140)),
              (1.9, (255, 10, 50)), (3.2, (255, 120, 0))],
    "toxic": [(0.0, (0, 0, 0)), (0.15, (0, 15, 40)), (0.6, (0, 60, 150)), (1.0, (0, 220, 120)),
              (1.9, (60, 255, 0)), (3.2, (220, 255, 0))],
    "abyss": [(0.0, (0, 0, 0)), (0.15, (25, 0, 0)), (0.6, (100, 0, 10)), (1.0, (255, 0, 0)),
              (1.9, (255, 0, 80)), (3.2, (190, 0, 255))],
}


def palette(v, stops):
    out = np.zeros(v.shape + (3,))
    v = np.clip(v, stops[0][0], stops[-1][0])
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        m = (v >= a) & (v <= b)
        f = ((v - a) / (b - a))[m][:, None]
        out[m] = np.array(ca) * (1 - f) + np.array(cb) * f
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="images/experiments/lavalamp")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--blobs", type=int, default=7)
    ap.add_argument("--scheme", choices=sorted(SCHEMES), default="magma")
    ap.add_argument("--seed", type=int, default=2)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    stops = SCHEMES[a.scheme]
    blobs = [dict(x=rnd.uniform(10, 36), ax=rnd.uniform(1.5, 4.5), fx=rnd.choice([1, 1, 2]), px=rnd.uniform(0, 6.3),
                  y=rnd.uniform(18, 40), ay=rnd.uniform(7, 12), fy=1, py=rnd.uniform(0, 6.3),
                  r=rnd.uniform(3.6, 5.4), ar=rnd.uniform(0.1, 0.25), fr=rnd.choice([1, 2, 3]), pr=rnd.uniform(0, 6.3))
             for _ in range(a.blobs)]
    xs, ys = grid(SS)
    rr = rho(xs, ys)

    def render(t):
        ph = 2 * math.pi * t
        f = np.zeros(xs.shape)
        for b in blobs:
            x = b["x"] + b["ax"] * math.sin(b["fx"] * ph + b["px"])
            y = b["y"] + b["ay"] * math.sin(b["fy"] * ph + b["py"])
            r = b["r"] * (1 + b["ar"] * math.sin(b["fr"] * ph + b["pr"]))
            d2 = ((xs - x) ** 2 + (ys - y) ** 2) / (r * r)
            f += 2.4 * np.clip(1 - d2 / 4, 0, None) ** 3  # compact kernel: ~1.0 at d = r, 0 beyond 2r
        # the lamp's warm floor and cool ceiling: a little extra field at the chin
        f += 0.3 * smooth(44, 58, ys)
        f *= 1 - smooth(0.9, 1.02, rr)
        return down(palette(f, stops), SS)

    save(blur_frames(render, a.frames), a.out)


if __name__ == "__main__":
    main()
