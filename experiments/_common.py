"""Shared helpers for the experiment generators (preview-only; no mask / Bluetooth here).

Conventions match spiral.py / tendrils.py: 46x58 RGB frames, usable oval centre (23, 29)
radii 21.5 x 27, eye holes from eyes.json. Each generator writes into its output folder:
  frameNN.png  exactly 46x58 RGB (cells that have no LED are written black)
  preview.gif  6x nearest upscale, 1000 ms per frame (the real ~1 fps slot playback)
  sheet.png    all frames at 4x; cells without LEDs (outside the oval, eye holes) are tinted
               dark grey on the sheet only, so the layout can be judged
"""
import json
import os

import numpy as np
from PIL import Image

W, H = 46, 58
CX, CY = 23.0, 29.0
RX, RY = 21.5, 27.0
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_eyes = json.load(open(os.path.join(ROOT, "eyes.json"), encoding="utf-8"))["eyes"]
EYES = [dict(bbox=e["bbox"], center=tuple(e["center"]), cells=[tuple(c) for c in e["cells"]]) for e in _eyes]

_ys, _xs = np.mgrid[0:H, 0:W]
OVAL = (((_xs + 0.5 - CX) / RX) ** 2 + ((_ys + 0.5 - CY) / RY) ** 2) <= 1.0
EYE = np.zeros((H, W), bool)
for _e in EYES:
    for _x, _y in _e["cells"]:
        EYE[_y, _x] = True
LIVE = OVAL & ~EYE  # cells that actually have an LED


def grid(ss=1):
    """LED-unit coordinates of (sub)pixel centres, shape (H*ss, W*ss)."""
    ys, xs = np.mgrid[0 : H * ss, 0 : W * ss]
    return (xs + 0.5) / ss, (ys + 0.5) / ss


def down(a, ss):
    """Box-downsample an (H*ss, W*ss[, C]) array to (H, W[, C])."""
    if ss == 1:
        return a
    sh = (H, ss, W, ss) + a.shape[2:]
    return a.reshape(sh).mean(axis=(1, 3))


def rho(xs, ys):
    """Normalised elliptical radius (1.0 = oval edge)."""
    return np.hypot((xs - CX) / RX, (ys - CY) / RY)


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def eye_dist(xs, ys, eye=None, dx=0.0, dy=0.0):
    """Distance (LEDs) from each point to the nearest eye-hole cell centre (optionally one eye,
    optionally with the eye cells shifted by dx, dy)."""
    cells = [c for i, e in enumerate(EYES) if eye is None or i == eye for c in e["cells"]]
    c = np.array(cells, float) + 0.5
    c[:, 0] += dx
    c[:, 1] += dy
    best = np.full(xs.shape, 1e9)
    for cx, cy in c:
        best = np.minimum(best, np.hypot(xs - cx, ys - cy))
    return best


def blur_frames(render, n, sub=4):
    """n looped frames, each the 0.55*mean + 0.45*max of `sub` sub-frames (spiral20 recipe).
    render(t) -> float array (H, W, 3) 0..255 for loop time t in [0, 1)."""
    out = []
    for i in range(n):
        g = np.stack([render((i + k / sub) / n) for k in range(sub)])
        out.append(0.55 * g.mean(0) + 0.45 * g.max(0))
    return out


def to_img(a):
    a = np.clip(np.asarray(a, float), 0, 255)
    a = a * LIVE[..., None]
    return Image.fromarray(a.round().astype(np.uint8), "RGB")


def save(frames, outdir, cols=10):
    """frames: list of float (H, W, 3) arrays or PIL images. Writes frames, preview.gif, sheet.png."""
    imgs = [f if isinstance(f, Image.Image) else to_img(f) for f in frames]
    os.makedirs(outdir, exist_ok=True)
    for p in os.listdir(outdir):  # drop stale frames from a longer earlier run
        if p.startswith("frame") and p.endswith(".png"):
            os.remove(os.path.join(outdir, p))
    for i, f in enumerate(imgs):
        assert f.size == (W, H) and f.mode == "RGB"
        f.save(os.path.join(outdir, f"frame{i + 1:02d}.png"))
    big = [f.resize((W * 6, H * 6), Image.NEAREST) for f in imgs]
    big[0].save(os.path.join(outdir, "preview.gif"), save_all=True, append_images=big[1:],
                duration=1000, loop=0, disposal=1, optimize=False)
    s, gap = 4, 3
    cols = min(cols, len(imgs))
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (W * s + gap) + gap, rows * (H * s + gap) + gap), (60, 60, 70))
    for i, f in enumerate(imgs):
        a = np.asarray(f).copy()
        a[~LIVE & (a.max(-1) == 0)] = (22, 22, 30)
        tile = Image.fromarray(a).resize((W * s, H * s), Image.NEAREST)
        sheet.paste(tile, (gap + (i % cols) * (W * s + gap), gap + (i // cols) * (H * s + gap)))
    sheet.save(os.path.join(outdir, "sheet.png"))
    print(f"wrote {len(imgs)} frames to {outdir}")


def periodic_noise(xs, ys, t, seed, terms=6, kmin=0.15, kmax=0.6, tmax=2):
    """Sum of travelling sine waves with whole-number temporal cycles per loop -> seamless.
    Returns roughly -1..1. Same seed -> same field shape at every t."""
    import random
    rnd = random.Random(seed)
    acc = np.zeros(np.shape(xs))
    for _ in range(terms):
        k = rnd.uniform(kmin, kmax)
        ang = rnd.uniform(0, 2 * np.pi)
        n = rnd.choice([c for c in range(-tmax, tmax + 1) if c])
        p = rnd.uniform(0, 2 * np.pi)
        acc += np.sin(k * (np.cos(ang) * xs + np.sin(ang) * ys) + 2 * np.pi * n * t + p)
    return acc / np.sqrt(terms * 0.5)
