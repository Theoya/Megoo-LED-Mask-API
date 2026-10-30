"""Glyph rain: columns of original 3x4 glyphs lit by drops falling down the face.

The glyphs are generated from random stroke combinations (no existing alphabet). They stay
in place; what moves is a bright head with a fading trail sliding down each column, about one
glyph row per frame, which reads well with hard cuts at 1 fps. Glyph cells that would touch
an eye hole (+1 cell margin) are left out, so the rain parts around the eyes, and the eye rims
glow faintly whenever a drop passes them. A few glyphs mutate each frame; everything is
indexed by frame number, so the loop is seamless.

  python experiments/glyphrain.py [--out images/experiments/glyphrain --frames 20 --color 00f0ff]
"""
import argparse
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import EYE, H, LIVE, W, grid, save  # noqa: E402

GW, GH = 3, 4  # glyph size; cells are (GW+1) x (GH+1) with a 1-LED gap


def make_glyphs(rnd, n):
    strokes = []
    for x in range(GW):
        strokes.append({(x, y) for y in range(GH)})            # verticals
    for y in range(GH):
        strokes.append({(x, y) for x in range(GW)})            # horizontals
    strokes += [{(0, 0), (1, 1), (2, 2)}, {(2, 0), (1, 1), (0, 2)}, {(0, 1), (1, 2), (2, 3)},
                {(2, 1), (1, 2), (0, 3)}, {(1, 0)}, {(1, 3)}, {(0, 0), (0, 1)}, {(2, 2), (2, 3)},
                {(0, 3), (1, 3)}, {(1, 0), (2, 0)}, {(0, 0), (0, 1), (1, 1)}, {(2, 2), (1, 2), (1, 3)},
                {(1, 1), (1, 2)}, {(0, 2), (2, 2)}, {(0, 0), (2, 0)}, {(0, 3), (2, 3)}]
    for x in range(GW):  # half-height strokes give a more runic, less Latin look
        strokes += [{(x, 0), (x, 1)}, {(x, 2), (x, 3)}]
    out, seen = [], set()
    while len(out) < n:
        g = set()
        for s in rnd.sample(strokes, rnd.choice([2, 3, 3, 4])):
            g |= s
        key = frozenset(g)
        if 5 <= len(g) <= 9 and key not in seen:
            seen.add(key)
            out.append(sorted(g))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="images/experiments/glyphrain")
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--color", default="00f0ff", help="head colour; the trail cools toward --trail")
    ap.add_argument("--trail", default="0030ff")
    ap.add_argument("--floor", type=float, default=0.07, help="brightness of unlit glyphs")
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    head = np.array([int(a.color[i : i + 2], 16) for i in (0, 2, 4)], float)
    tail = np.array([int(a.trail[i : i + 2], 16) for i in (0, 2, 4)], float)
    glyphs = make_glyphs(rnd, 60)

    near_eye = np.zeros((H, W), bool)  # eye holes grown by one cell
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            near_eye |= np.roll(np.roll(EYE, dy, 0), dx, 1)

    cols = []
    for c in range(11):
        x0 = 1 + c * (GW + 1)
        yoff = rnd.randint(-4, 0)
        cells = []
        for r in range(13):
            y0 = yoff + r * (GH + 1)
            px = [(x0 + gx, y0 + gy) for gx in range(GW) for gy in range(GH)]
            if any(0 <= y < H and near_eye[y, x] for x, y in px):
                continue  # rain parts around the eye holes
            if not any(0 <= y < H and LIVE[y, x] for x, y in px):
                continue
            cells.append(dict(x0=x0, y0=y0, g=rnd.randrange(len(glyphs)),
                              mut=[rnd.random() < 0.12 for _ in range(a.frames)],
                              alt=[rnd.randrange(len(glyphs)) for _ in range(a.frames)]))
        span = rnd.uniform(70, 96)  # head travel per loop (one pass per loop -> 3.5-4.8 LEDs/frame)
        nd = rnd.choice([2, 2, 3])
        d0 = rnd.random()
        drops = [(d0 + k / nd + rnd.uniform(-0.08, 0.08)) % 1.0 for k in range(nd)]
        cols.append(dict(cells=cells, span=span, drops=drops, trail=rnd.uniform(20, 34)))

    xs, ys = grid(1)
    frames = []
    for i in range(a.frames):
        t = i / a.frames
        img = np.zeros((H, W, 3))
        eye_glow = np.zeros((H, W))
        for col in cols:
            heads = [-10 + col["span"] * ((t + d) % 1.0) for d in col["drops"]]
            for cell in col["cells"]:
                cy = cell["y0"] + GH / 2
                b, hot = a.floor, 0.0
                for hy in heads:
                    dist = hy - cy  # >0: head has passed this glyph
                    if -2.5 <= dist <= 2.5:
                        b, hot = 1.0, 1.0
                    elif dist > 2.5:
                        v = 0.6 * np.exp(-(dist - 2.5) / col["trail"] * 2.0)
                        if v > b:
                            b, hot = v, 0.0
                rgb = head * hot + (tail * (1 - b) + head * b) * (1 - hot) if b > a.floor else tail
                rgb = rgb * (b if not hot else 1.0)
                gi = cell["alt"][i] if cell["mut"][i] else cell["g"]
                for gx, gy in glyphs[gi]:
                    x, y = cell["x0"] + gx, cell["y0"] + gy
                    if 0 <= y < H and 0 <= x < W:
                        img[y, x] = np.maximum(img[y, x], rgb)
            # columns passing near an eye light its rim
            xc = col["cells"][0]["x0"] + 1.5 if col["cells"] else 0
            for hy in heads:
                eye_glow = np.maximum(eye_glow, np.exp(-((xs - xc) ** 2) / 18 - ((ys - hy) ** 2) / 30))
        rim = (near_eye & ~EYE).astype(float)  # the one-cell ring around each eye hole
        rim_rgb = rim[..., None] * (0.15 * tail + 0.75 * eye_glow[..., None] * head)
        img = np.maximum(img, rim_rgb)
        frames.append(img)
    save(frames, a.out)


if __name__ == "__main__":
    main()
