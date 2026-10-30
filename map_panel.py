"""Find which grid positions have no visible LED (eye holes, oval edge) from a webcam photo.

  python map_panel.py pattern images/map_pattern.png      # make the test image, show it on the mask
  python map_panel.py analyze photo.jpg                   # -> panel_map.json + panel_map.png

The pattern lights every LED dim white plus four coloured 3x3 markers at known grid
positions. The markers give a homography from grid to photo, then each cell is sampled.
"""
import json
import sys

import cv2
import numpy as np
from PIL import Image

W, H = 46, 58
# All markers are red: on this webcam the dim-white background reads as saturated blue, and
# yellow reads as green, so only red is unambiguous. Markers are told apart by position hints.
MARKERS = {  # name: (grid x, grid y)
    "tl": (10, 12),
    "tr": (35, 12),
    "bl": (10, 42),
    "br": (35, 42),
}


def pattern(out):
    im = Image.new("RGB", (W, H), (70, 70, 70))
    px = im.load()
    for gx, gy in MARKERS.values():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                px[gx + dx, gy + dy] = (255, 0, 0)
    im.save(out)
    print(out)


def find_marker(hsv, name, hint=None, radius=110):
    """Locate a marker; with a hint (photo x,y) only search a window around it."""
    if hint:
        x0, y0 = max(0, int(hint[0]) - radius), max(0, int(hint[1]) - radius)
        win = hsv[y0 : int(hint[1]) + radius, x0 : int(hint[0]) + radius]
        cx, cy = _find(win, name)
        return cx + x0, cy + y0
    return _find(hsv, name)


def _find(hsv, name):
    # red LEDs read as red-to-magenta (hue 136-180 or 0-10) on this webcam
    mask = cv2.inRange(hsv, np.array((136, 70, 110)), np.array((180, 255, 255))) | cv2.inRange(
        hsv, np.array((0, 70, 110)), np.array((10, 255, 255))
    )
    if not mask.any():
        raise SystemExit(f"marker {name} not found")
    # biggest blob cluster: dilate first so the 3x3 dots merge
    mask = cv2.dilate((mask > 0).astype(np.uint8), np.ones((15, 15), np.uint8))
    n, lab, stats, cents = cv2.connectedComponentsWithStats(mask)
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return cents[i]


def analyze(photo, hints=None):
    img = cv2.imread(photo)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    src, dst = [], []
    for name, (gx, gy) in MARKERS.items():
        cx, cy = find_marker(hsv, name, (hints or {}).get(name))
        src.append((gx, gy))
        dst.append((cx, cy))
        print(f"{name}: grid ({gx},{gy}) -> photo ({cx:.0f},{cy:.0f})")
    Hm, _ = cv2.findHomography(np.float32(src), np.float32(dst))
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (0, 0), 3)
    pts = np.float32([[(x, y) for x in range(W)] for y in range(H)]).reshape(-1, 1, 2)
    proj = cv2.perspectiveTransform(pts, Hm).reshape(H, W, 2)
    val = np.zeros((H, W))
    for y in range(H):
        for x in range(W):
            px, py = proj[y, x]
            if 0 <= px < img.shape[1] and 0 <= py < img.shape[0]:
                val[y, x] = gray[int(py), int(px)]
    lit_level = np.percentile(val[val > 0], 60)
    on = val > 0.45 * lit_level
    rows = ["".join("#" if on[y, x] else "." for x in range(W)) for y in range(H)]
    json.dump({"width": W, "height": H, "rows": rows}, open("panel_map.json", "w"), indent=1)
    Image.fromarray((on * 255).astype(np.uint8)).resize((W * 8, H * 8), Image.NEAREST).save("panel_map.png")
    # annotated photo for checking the projection
    for y in range(H):
        for x in range(W):
            cv2.circle(img, (int(proj[y, x][0]), int(proj[y, x][1])), 2, (0, 255, 0) if on[y, x] else (0, 0, 255), -1)
    cv2.imwrite("panel_map_check.jpg", img)
    print("\n".join(rows))


if __name__ == "__main__":
    if sys.argv[1] == "pattern":
        pattern(sys.argv[2])
    else:  # optional hints: red=x,y green=x,y ...
        hints = {k: tuple(map(float, v.split(","))) for k, v in (h.split("=") for h in sys.argv[3:])}
        analyze(sys.argv[2], hints)
