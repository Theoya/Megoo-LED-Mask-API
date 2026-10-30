"""Heartbeat: a dark-red knot in the middle of the face beats once per loop and the pulse runs
out along a branching vein network to the edge of the oval, then the face sinks back to a
faint glow and waits.

The veins grow from the centre as random-walk branches that steer away from the eye holes
(they wrap around the eyes rather than crossing them). Timing is built for ~1 s hard cuts:
frame 1 = lub, frame 2 = dub, then the pulse front travels outward about a fifth of the way
per frame, followed by a few dim resting frames so each beat lands as an event.

  python experiments/heartbeat.py [--out images/experiments/heartbeat --frames 10]
"""
import argparse
import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import EYE, down, eye_dist, grid, rho, save, smooth  # noqa: E402

SS = 4
HX, HY = 23.0, 31.0  # the knot sits between the eyes and the mouth


def grow(rnd, ed_fn):
    """Return vein points [(x, y, path_dist, width)]."""
    pts = []
    stack = [(HX, HY, a, 0.0, 1.35) for a in np.linspace(0, 2 * math.pi, 9, endpoint=False) + rnd.uniform(0, 0.8)]
    while stack:
        x, y, ang, s, w = stack.pop()
        for _ in range(80):
            ang += rnd.gauss(0, 0.22)
            nx, ny = x + 0.5 * math.cos(ang), y + 0.5 * math.sin(ang)
            d = ed_fn(nx, ny)
            if d < 2.6:  # steer around the eye: turn away from the nearest eye centre
                ex, ey = (10.4, 17.3) if nx < 23 else (33.6, 16.9)
                away = math.atan2(ny - ey, nx - ex)
                diff = (away - ang + math.pi) % (2 * math.pi) - math.pi
                ang += 0.5 * diff
                nx, ny = x + 0.5 * math.cos(ang), y + 0.5 * math.sin(ang)
                if ed_fn(nx, ny) < 1.6:
                    break
            if math.hypot((nx - 23) / 21.5, (ny - 29) / 27) > 0.97:
                break
            x, y, s = nx, ny, s + 0.5
            w = max(0.45, w * 0.985)
            pts.append((x, y, s, w))
            if s > 3 and rnd.random() < 0.055 and w > 0.55:
                stack.append((x, y, ang + rnd.choice([-1, 1]) * rnd.uniform(0.5, 0.9), s, w * 0.8))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="images/experiments/heartbeat")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--seed", type=int, default=12)
    a = ap.parse_args()
    rnd = random.Random(a.seed)

    cells = np.argwhere(EYE)[:, ::-1] + 0.5

    def ed_fn(x, y):
        return float(np.min(np.hypot(cells[:, 0] - x, cells[:, 1] - y)))

    pts = np.array(grow(rnd, ed_fn))
    L = pts[:, 2].max()
    xs, ys = grid(SS)
    # nearest vein point and distance for every sub-pixel
    best = np.full(xs.shape, 1e9)
    idx = np.zeros(xs.shape, int)
    for i, (px, py, _, _) in enumerate(pts):
        d = np.hypot(xs - px, ys - py)
        m = d < best
        best[m], idx[m] = d[m], i
    s_at, w_at = pts[idx, 2], pts[idx, 3]
    vein = smooth(1.0, 0.35, best / w_at)  # 1 on the vein, 0 off it
    core_d = np.hypot((xs - HX) / 1.0, (ys - HY) / 1.15)
    r = rho(xs, ys)
    ed = eye_dist(xs, ys)

    th = np.arctan2(ys - HY, xs - HX)
    # beat schedule in "beat units" (12 per loop; scales with --frames):
    # 0 lub, 1 dub, 1-7 pulse runs to the rim, 7-11 blood seeps back inward, 11 the knot clenches
    n = a.frames
    frames = []
    for i in range(n):
        t = i / n * 12
        lub = math.exp(-(t / 0.6) ** 2) + 0.85 * math.exp(-((t - 1) / 0.6) ** 2)
        clench = math.exp(-((t - 11) / 0.7) ** 2)
        front = (t - 0.5) / 6.0 * L  # reaches the rim around t=6.5
        reach = (1 - smooth(-6, 2, s_at - front))  # veins the pulse has passed
        after = reach * np.exp(-np.clip(front - s_at, 0, None) / (0.35 * L)) * max(0.0, 1 - (t - 1) / 7)
        wave = np.exp(-((s_at - front) / 3.2) ** 2) * (1.0 - 0.45 * min(1, max(0, t - 1) / 6)) * (t < 7.5)
        back = L * (1 - (t - 7) / 4.5)  # slow faint return wave
        ret = 0.22 * np.exp(-((s_at - back) / 4.0) ** 2) * (7 <= t <= 11.5)
        v = vein * np.clip(0.09 + 0.85 * wave + 0.35 * after + ret, 0, 1)
        # the knot: a lumpy hollow ring that swells on lub/dub and clenches small before the next beat
        rad = (2.7 + 1.7 * lub - 0.9 * clench) * (1 + 0.16 * np.sin(3 * th + 0.7) + 0.08 * np.sin(5 * th + 2 + t))
        knot = smooth(rad + 0.8, rad - 0.6, core_d) * (0.3 + 0.7 * smooth(0.4, 1.7, core_d))
        kb = 0.3 + 0.7 * lub + 0.25 * clench
        # faint flush across the whole face following the front, and the eye rims catching it
        flush = 0.10 * np.exp(-((r * 27 - front * 0.8) / 4) ** 2) * (t < 7.5)
        rims = np.exp(-ed / 1.1) * (0.10 + 0.45 * math.exp(-((t - 3.2) / 1.4) ** 2))
        val = np.maximum.reduce([v, knot * kb, flush * np.ones_like(v), rims])
        hot = np.clip(np.maximum(wave * vein, knot * lub) - 0.6, 0, 1) / 0.4  # orange tinge at the peaks
        rgb = np.stack([255 * val, 55 * hot * val, 10 * hot * val], -1)
        rgb *= (1 - smooth(0.95, 1.02, r))[..., None]
        frames.append(down(rgb ** 1.0, SS))
    save(frames, a.out)


if __name__ == "__main__":
    main()
