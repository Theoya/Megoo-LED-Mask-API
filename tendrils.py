"""Generate a looping 'creepy tendrils' animation of the starburst logo for the full panel.

Each ray from the source icon becomes a tapered tendril that sways, curls and
stretches/shrinks with its own phase, so they move independently. All motion
uses whole-number cycles per loop, so the last frame flows into the first.

  python tendrils.py images/claude_symbol_source.webp images/tendrils --frames 10
"""
import argparse
import math
import os
import random

from PIL import Image, ImageDraw

from starburst import measure_rays

W, H, SS = 46, 58, 8
RX, RY = 21.5, 27.0  # usable oval radii in LEDs (panel corners don't exist)


def frame(rays, t, params, color, hub):
    img = Image.new("RGB", (W * SS, H * SS))
    d = ImageDraw.Draw(img)
    cx, cy = W * SS / 2, H * SS / 2
    for (ang, _), p in zip(rays, params):
        ph = 2 * math.pi * t
        sway = p["sway"] * math.sin(p["fs"] * ph + p["p1"])
        length = 0.78 + p["grow"] * math.sin(p["fg"] * ph + p["p2"])  # fraction of the oval edge
        curl = p["curl"] * math.sin(p["fc"] * ph + p["p3"])
        a0 = ang + sway
        # distance to the oval edge along this direction
        edge = 1 / math.sqrt((math.cos(a0) / RX) ** 2 + (math.sin(a0) / RY) ** 2)
        L = edge * length * SS
        steps = 40
        for i in range(steps + 1):
            s = i / steps
            # bend grows toward the tip and wiggles along the length
            a = a0 + curl * s * s + 0.12 * math.sin(6 * s - 3 * ph * p["fs"] + p["p1"]) * s
            r = s * L
            x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            w = (2.2 * (1 - s) + 0.6) * SS  # taper: thick at the hub, thin at the tip
            d.ellipse([x - w, y - w, x + w, y + w], fill=color)
    hr = hub * SS
    d.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=color)
    return img.resize((W, H), Image.BOX)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("outdir")
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--color", default="ff4600")
    ap.add_argument("--hub", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    rays = measure_rays(Image.open(a.src).convert("RGBA").split()[3])
    rnd = random.Random(a.seed)
    params = [
        dict(
            sway=math.radians(rnd.uniform(5, 11)), fs=rnd.choice([1, 2]), p1=rnd.uniform(0, 6.3),
            grow=rnd.uniform(0.12, 0.22), fg=rnd.choice([1, 1, 2]), p2=rnd.uniform(0, 6.3),
            curl=math.radians(rnd.uniform(10, 30)) * rnd.choice([-1, 1]), fc=rnd.choice([1, 2]), p3=rnd.uniform(0, 6.3),
        )
        for _ in rays
    ]
    color = tuple(int(a.color[i : i + 2], 16) for i in (0, 2, 4))
    os.makedirs(a.outdir, exist_ok=True)
    frames = [frame(rays, i / a.frames, params, color, a.hub) for i in range(a.frames)]
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
