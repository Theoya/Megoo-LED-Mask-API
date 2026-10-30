"""Generate a looping 'bottomless' red spiral shaped to the mask's oval.

The spiral lives in normalised elliptical coordinates (x/RX, y/RY), so its arms
hug the oval outline at the rim and wind inward along a log spiral, thinning and
darkening until they vanish into a black void in the middle. Each loop rotates
the k-armed spiral by exactly one arm spacing (360/k degrees), and every wobble
and pulse uses whole-number cycles per loop, so the last frame flows into the first.

  python spiral.py images/spiral --frames 10 --arms 3
"""
import argparse
import math
import os
import random

import numpy as np
from PIL import Image

W, H, SS = 46, 58, 8
CX, CY = 23.0, 29.0
RX, RY = 21.5, 27.0  # usable oval radii in LEDs (panel corners don't exist)


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def frame(t, a, dark_ang, wob):
    ys, xs = np.mgrid[0 : H * SS, 0 : W * SS]
    u = ((xs + 0.5) / SS - CX) / RX
    v = ((ys + 0.5) / SS - CY) / RY
    rho = np.maximum(np.hypot(u, v), 1e-4)
    th = np.arctan2(v, u)
    ph = 2 * math.pi * t
    lr = np.log(rho)
    # uneven, breathing wobble: whole-number cycles in t keep the loop seamless
    wobble = sum(amp * np.sin(m * th + n * ph + p + q * lr) for amp, m, n, p, q in wob)
    # log spiral: phase advances with angle and ln(radius); -ph rotates by one arm spacing per loop
    phase = a.arms * th + a.twist * lr - ph + wobble
    # uneven arms: a fixed dim sector the arms rotate through (fixed in space, so it loops)
    shade = 1 - a.uneven * (0.5 + 0.5 * np.cos(th - dark_ang + 1.5 * lr))
    c = np.cos(phase)
    # arm half-width (in cos space) shrinks toward the middle so arms taper into the void
    width = a.width * (0.25 + 0.75 * smooth(0.0, 0.9, rho))
    band = smooth(1 - width, 1 - width * 0.35, c)
    # depth fade: bright at the rim, sinking to nothing in the middle
    depth = smooth(a.void, 0.95, rho) ** a.gamma
    # slow pulse sucked inward (one cycle per loop)
    pulse = 1 - a.pulse * (0.5 + 0.5 * np.sin(ph + 6.0 * lr))
    # soft rim so nothing is hard-clipped at the oval edge
    rim = 1 - smooth(0.96, 1.04, rho)
    val = band * depth * pulse * rim * shade
    # faint dark-red haze between arms near the rim (the "glow of the pit wall")
    haze = 0.14 * smooth(0.35, 0.95, rho) * rim * (0.5 + 0.5 * c)
    val = np.clip(np.maximum(val, haze), 0, 1)
    r, g, b = a.color
    # deep reds at low values, saturated red at the top: square-ish curve keeps dim parts deep
    lum = val ** 1.3
    rgb = np.stack([lum * r, lum * g, lum * b], axis=-1)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    return img.resize((W, H), Image.BOX)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--arms", type=int, default=3)
    ap.add_argument("--twist", type=float, default=10.0, help="winding per e-fold of radius (higher = tighter)")
    ap.add_argument("--width", type=float, default=1.1, help="arm thickness at the rim (0..2)")
    ap.add_argument("--void", type=float, default=0.05, help="normalised radius of the black centre")
    ap.add_argument("--gamma", type=float, default=1.0, help="how fast arms darken toward the centre")
    ap.add_argument("--pulse", type=float, default=0.25, help="depth of the inward-sucking pulse")
    ap.add_argument("--uneven", type=float, default=0.35, help="how much one side of the pit is dimmer")
    ap.add_argument("--color", default="ff0000")
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()
    a.color = tuple(int(a.color[i : i + 2], 16) for i in (0, 2, 4))

    rnd = random.Random(a.seed)
    dark_ang = rnd.uniform(0, 2 * math.pi)
    wob = [  # (amplitude, angular freq, cycles per loop, phase, radial twist)
        (rnd.uniform(0.25, 0.4), 1, 1, rnd.uniform(0, 6.3), rnd.uniform(0.5, 1.5)),
        (rnd.uniform(0.15, 0.25), 2, -1, rnd.uniform(0, 6.3), rnd.uniform(-1, 1)),
    ]
    os.makedirs(a.outdir, exist_ok=True)
    frames = [frame(i / a.frames, a, dark_ang, wob) for i in range(a.frames)]
    for i, f in enumerate(frames):
        f.save(os.path.join(a.outdir, f"frame{i + 1:02d}.png"))
    big = [f.resize((W * 6, H * 6), Image.NEAREST) for f in frames]
    big[0].save(os.path.join(a.outdir, "preview.gif"), save_all=True, append_images=big[1:], duration=150, loop=0)
    sheet = Image.new("RGB", (W * 4 * len(frames), H * 4))
    for i, f in enumerate(frames):
        sheet.paste(f.resize((W * 4, H * 4), Image.NEAREST), (i * W * 4, 0))
    sheet.save(os.path.join(a.outdir, "sheet.png"))
    print(f"wrote {len(frames)} frames to {a.outdir}")


if __name__ == "__main__":
    main()
