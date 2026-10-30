"""Embers: a low fire burning along the chin with sparks breaking off and rising past the eyes.

The flame field is a sum of upward-travelling waves whose vertical wavenumbers are whole
multiples of 2*pi/D, so after one loop every wave has moved exactly a whole number of
wavelengths (seamless). Each spark rises a whole number of times per loop, sways with a
whole-number frequency and flickers on its own clock. The eye holes catch a faint orange
underglow that flickers with the fire. Motion blur: 4 sub-frames per frame (spiral20 recipe).

  python experiments/embers.py [--out images/experiments/embers --frames 20 --sparks 26]
"""
import argparse
import math
import random
import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import H, W, blur_frames, eye_dist, grid, down, rho, save, smooth  # noqa: E402

SS = 4
# fire palette: (value, rgb); stops are saturated, no white (pastels wash out on the mask)
PAL = [(0.00, (0, 0, 0)), (0.18, (60, 0, 0)), (0.40, (170, 4, 0)), (0.62, (255, 30, 0)),
       (0.82, (255, 95, 0)), (1.00, (255, 170, 10))]


def palette(v):
    v = np.clip(v, 0, 1)
    out = np.zeros(v.shape + (3,))
    for (a, ca), (b, cb) in zip(PAL, PAL[1:]):
        m = (v >= a) & (v <= b)
        f = ((v - a) / (b - a))[m][:, None]
        out[m] = np.array(ca) * (1 - f) + np.array(cb) * f
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="images/experiments/embers")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--sparks", type=int, default=26)
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args()
    rnd = random.Random(a.seed)

    xs, ys = grid(SS)
    r = rho(xs, ys)
    D = 30.0  # vertical repeat of the flame waves (LEDs)
    waves = []  # (amp, ky, kx, phase); ky = 2*pi*m/D so the upward scroll loops
    for i in range(9):
        m = rnd.choice([1, 2, 2, 3, 3, 4, 5])
        kx = rnd.uniform(0.35, 1.1) * rnd.choice([-1, 1])
        waves.append((rnd.uniform(0.6, 1.0) / (1 + 0.25 * i), 2 * math.pi * m / D, kx, rnd.uniform(0, 6.3)))
    norm = math.sqrt(sum(w[0] ** 2 for w in waves) / 2)
    # tongues: slow sideways-drifting bumps in flame height, whole-number cycles per loop
    tongues = [(rnd.uniform(0.10, 0.18), rnd.uniform(0.25, 0.6), rnd.choice([-1, 1]), rnd.uniform(0, 6.3))
               for _ in range(3)]
    sparks = [dict(x0=rnd.uniform(6, 40), cyc=rnd.choice([1, 1, 1, 2]), off=rnd.random(),
                   sway=rnd.uniform(0.8, 2.5), fs=rnd.choice([1, 2, 3]), ps=rnd.uniform(0, 6.3),
                   fl=rnd.choice([3, 5, 7, 9]), pf=rnd.uniform(0, 6.3), top=rnd.uniform(4, 16),
                   bright=rnd.uniform(0.6, 1.0)) for _ in range(a.sparks)]
    under = eye_dist(xs, ys)
    ecy = np.where(xs < 23, 18.5, 17.5)  # rough eye-hole vertical middles

    def render(t):
        ph = 2 * math.pi * t
        warp = 1.6 * np.sin(0.5 * ys + 2 * math.pi * 2 * t + 0.3 * xs)
        n = sum(amp * np.sin(kx * (xs + warp) + ky * (ys + D * t) + p) for amp, ky, kx, p in waves) / norm
        tg = sum(amp * np.sin(k * xs + c * ph + p) for amp, k, c, p in tongues)
        lift = 0.06 * math.sin(3 * ph) + 0.04 * math.sin(7 * ph + 1.0)
        h = (ys - 33.0) / 22.0  # 0 at y=33 -> 1 at the chin
        v = 1.3 * h + 0.30 * n * (0.6 + 0.6 * np.clip(1 - h, 0, 1)) + tg - 0.42 + lift
        v = np.clip(v, 0, 1) * (1 - smooth(0.93, 1.02, r))
        rgb = palette(v ** 1.1) * 1.0
        # sparks: small gaussian blobs, hot orange at the base, cooling to red as they rise
        sp = np.zeros(xs.shape)
        hot = np.zeros(xs.shape)
        for s in sparks:
            f = (s["cyc"] * t + s["off"]) % 1.0
            y = 50.0 - (50.0 - s["top"]) * f
            x = s["x0"] + s["sway"] * math.sin(s["fs"] * ph + s["ps"]) + 2.0 * f * math.sin(s["ps"])
            fade = (1 - f) ** 0.7 * min(1.0, f * 6)  # born in the fire, dies near the top
            flick = 0.55 + 0.45 * math.sin(s["fl"] * ph + s["pf"])
            b = s["bright"] * fade * flick
            g = np.exp(-((xs - x) ** 2 + (ys - y) ** 2) / (2 * 0.65 ** 2))
            sp = np.maximum(sp, b * g)
            hot = np.maximum(hot, b * g * (1 - f))
        spark_rgb = np.stack([255 * sp, 120 * hot + 25 * sp, 0 * sp], -1)
        rgb = np.maximum(rgb, spark_rgb * 1.8)
        # underglow on the lower rim of the eye holes, flickering with the fire
        glow = np.exp(-under / 1.3) * smooth(-1, 2.5, ys - ecy)
        gl = 0.35 + 0.12 * math.sin(3 * ph + 0.5) + 0.08 * math.sin(7 * ph)
        rgb = np.maximum(rgb, glow[..., None] * gl * np.array([255, 60, 0]))
        return down(rgb, SS)

    save(blur_frames(render, a.frames), a.out)


if __name__ == "__main__":
    main()
