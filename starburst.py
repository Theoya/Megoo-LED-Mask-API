"""Redraw a starburst icon as thin rays from a small hub, for the 16-row mask.

Shrinking the source straight down leaves a big blob with stubby rays. Instead this
measures each ray's angle and length from the source's alpha channel and redraws
them as lines, so the rays stay long at LED resolution.

  python starburst.py images/claude_symbol_source.webp images/starburst.png --size 18
"""
import argparse
import math

from PIL import Image, ImageDraw

MASK_W, MASK_H = 44, 16
SS = 8  # supersampling factor for anti-aliased drawing


def measure_rays(alpha: Image.Image):
    """Return [(angle_rad, length_frac)] for each ray, length as a fraction of the half-size."""
    w, h = alpha.size
    cx, cy, half = w / 2, h / 2, min(w, h) / 2
    px = alpha.load()

    def solid(ang, r):
        x, y = int(cx + r * math.cos(ang)), int(cy + r * math.sin(ang))
        return 0 <= x < w and 0 <= y < h and px[x, y] > 128

    # angles where the shape still exists at 60% radius = inside a ray
    probe = 0.6 * half
    hits = [solid(math.radians(d / 2), probe) for d in range(720)]
    start = hits.index(False)  # rotate so we begin in a gap
    runs, cur = [], None
    for i in range(720):
        k = (start + i) % 720
        if hits[k] and cur is None:
            cur = [k, k]
        elif hits[k]:
            cur[1] = k
        elif cur is not None:
            runs.append(cur)
            cur = None
    if cur is not None:
        runs.append(cur)

    rays = []
    for a, b in runs:
        if b < a:
            b += 720
        ang = math.radians(((a + b) / 2 % 720) / 2)
        r = probe
        while solid(ang, r + 1):
            r += 1
        rays.append((ang, r / half))
    return rays


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--size", type=int, default=16, help="diameter in LEDs (>16 crops top/bottom)")
    ap.add_argument("--color", default="ff4600")
    ap.add_argument("--ray", type=float, default=1.3, help="ray thickness in LEDs")
    ap.add_argument("--hub", type=float, default=2.0, help="centre radius in LEDs")
    ap.add_argument("--fill", action="store_true", help="extend every ray to the panel edge (uses the full 44 width)")
    ap.add_argument("--stretch", type=float, default=1.0, help="vertical stretch factor (>1 = taller)")
    ap.add_argument("--dy", type=float, default=0.0, help="move the centre down by this many LEDs (negative = up)")
    a = ap.parse_args()

    rays = measure_rays(Image.open(a.src).convert("RGBA").split()[3])
    print(f"{len(rays)} rays: " + ", ".join(f"{math.degrees(t):.0f}deg/{l:.2f}" for t, l in rays))

    color = tuple(int(a.color[i : i + 2], 16) for i in (0, 2, 4))
    W, H = MASK_W * SS, MASK_H * SS
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    cx, cy = W / 2, H / 2 + a.dy * SS
    radius = (a.size / 2 - 0.5) * SS
    for ang, frac in rays:
        if a.fill:
            c, s = abs(math.cos(ang)), abs(math.sin(ang))
            L = min((W / 2 - SS / 2) / c if c else 1e9, (H / 2 - SS / 2) / s if s else 1e9)
        else:
            L = radius * min(1.0, frac / max(f for _, f in rays))  # longest ray reaches the edge
        d.line([(cx, cy), (cx + L * math.cos(ang), cy + a.stretch * L * math.sin(ang))], fill=color, width=round(a.ray * SS))
    hr = a.hub * SS
    d.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=color)

    img.resize((MASK_W, MASK_H), Image.BOX).save(a.out)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
