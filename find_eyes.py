"""Locate the visor's eye holes in grid coordinates from a photo of the map pattern.

Heavy blur turns the LED dots into a solid glow; the eye holes stay dark. Dark blobs
inside the lit panel are mapped back to the 46x58 grid through the 4 red markers.

  python find_eyes.py photo.jpg tl=x,y tr=x,y bl=x,y br=x,y   -> eyes.json, eyes_check.png
"""
import json
import sys

import cv2
import numpy as np

from map_panel import MARKERS, find_marker

W, H = 46, 58


def main():
    photo = sys.argv[1]
    hints = {k: tuple(map(float, v.split(","))) for k, v in (h.split("=") for h in sys.argv[2:])}
    img = cv2.imread(photo)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    grid, pix = [], []
    for name, (gx, gy) in MARKERS.items():
        grid.append((gx, gy))
        pix.append(find_marker(hsv, name, hints.get(name)))
    to_grid, _ = cv2.findHomography(np.float32(pix), np.float32(grid))

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    glow = cv2.GaussianBlur(gray, (0, 0), 9)
    lit = glow > 0.5 * np.percentile(glow, 99)
    # the lit panel region, holes filled: everything inside its outer contour
    cnts, _ = cv2.findContours(lit.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    panel = np.zeros_like(gray)
    # convex hull, so an eye hole that notches the outline still counts as inside
    cv2.drawContours(panel, [cv2.convexHull(max(cnts, key=cv2.contourArea))], -1, 255, -1)
    holes = (panel > 0) & ~lit
    holes = cv2.morphologyEx(holes.astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(holes)

    def to_g(i):
        ys, xs = np.nonzero(lab == i)
        return cv2.perspectiveTransform(np.float32(np.c_[xs, ys]).reshape(-1, 1, 2), to_grid).reshape(-1, 2)

    # eye holes sit in the upper half; anything lower is an obstruction in front of the mask
    upper = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 200 and to_g(i)[:, 1].mean() < H / 2]
    blobs = sorted(upper, key=lambda i: -stats[i, cv2.CC_STAT_AREA])[:2]

    eyes = []
    for i in blobs:
        g = to_g(i)
        cells = sorted({(int(round(x)), int(round(y))) for x, y in g if 0 <= x < W and 0 <= y < H})
        xs_, ys_ = zip(*cells)
        eyes.append({
            "bbox": [min(xs_), min(ys_), max(xs_), max(ys_)],
            "center": [round(float(np.mean(g[:, 0])), 1), round(float(np.mean(g[:, 1])), 1)],
            "cells": [list(c) for c in cells],
        })
    eyes.sort(key=lambda e: e["center"][0])  # image-left first
    json.dump({"width": W, "height": H, "eyes": eyes}, open("eyes.json", "w"), indent=1)

    # grid preview: eye cells marked on the oval
    prev = np.zeros((H, W, 3), np.uint8)
    for y in range(H):
        for x in range(W):
            if ((x + 0.5 - W / 2) / 21.5) ** 2 + ((y + 0.5 - H / 2) / 27.0) ** 2 <= 1:
                prev[y, x] = (60, 60, 60)
    for e in eyes:
        for x, y in e["cells"]:
            prev[y, x] = (0, 0, 255)
    cv2.imwrite("eyes_check.png", cv2.resize(prev, (W * 8, H * 8), interpolation=cv2.INTER_NEAREST))
    for e in eyes:
        print(f"eye: bbox x{e['bbox'][0]}-{e['bbox'][2]} y{e['bbox'][1]}-{e['bbox'][3]}, center {e['center']}, {len(e['cells'])} cells")


if __name__ == "__main__":
    main()
