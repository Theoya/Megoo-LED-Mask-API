"""Panel geometry and eye-safe placement for the 46x58 DIY panel.

The panel is an oval (grid corners have no LEDs) with two eye holes in the visor,
measured from a webcam photo by find_eyes.py into eyes.json. place() scales an image
as large as possible while keeping its lit pixels off the outside of the oval and off
the eye-hole cells (+ margin).

eyes.json lookup: explicit path, $MASKAPI_EYES, <repo>/eyes.json, maskapi/eyes.json (bundled copy).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Sequence

from PIL import Image

from .protocol import PANEL_H, PANEL_W

# usable oval, same formula as spiral.py / claude_session.py / find_eyes.py
OVAL_CX, OVAL_CY, OVAL_RX, OVAL_RY = PANEL_W / 2, PANEL_H / 2, 21.5, 27.0
VALIGN = ("center", "top", "bottom")


def in_oval(x: int, y: int) -> bool:
    return ((x + 0.5 - OVAL_CX) / OVAL_RX) ** 2 + ((y + 0.5 - OVAL_CY) / OVAL_RY) ** 2 <= 1.0


def eyes_path(path: Optional[str] = None) -> Path:
    here = Path(__file__).resolve().parent
    for cand in (path, os.environ.get("MASKAPI_EYES"), here.parent / "eyes.json"):
        if cand and Path(cand).is_file():
            return Path(cand)
    if path:
        raise FileNotFoundError(path)
    return here / "eyes.json"


@dataclass
class PanelMap:
    eyes: List[dict] = field(default_factory=list)  # {"bbox", "center", "cells": [[x, y], ...]}
    width: int = PANEL_W
    height: int = PANEL_H
    source: Optional[str] = None

    @classmethod
    def load(cls, path: Optional[str] = None) -> "PanelMap":
        p = eyes_path(path)
        d = json.loads(p.read_text(encoding="utf-8"))
        if (d.get("width", PANEL_W), d.get("height", PANEL_H)) != (PANEL_W, PANEL_H):
            raise ValueError(f"{p}: expected a {PANEL_W}x{PANEL_H} map")
        return cls(eyes=d.get("eyes", []), source=str(p))

    def eye_cells(self) -> set:
        return {(int(x), int(y)) for e in self.eyes for x, y in e["cells"]}

    def blocked(self, margin: int = 1, avoid_eyes: bool = True) -> List[List[bool]]:
        """[y][x] True where a lit pixel must not go: outside the oval, or an eye cell
        grown by `margin` cells (square neighbourhood)."""
        grid = [[not in_oval(x, y) for x in range(PANEL_W)] for y in range(PANEL_H)]
        if avoid_eyes:
            m = max(0, int(margin))
            for ex, ey in self.eye_cells():
                for y in range(ey - m, ey + m + 1):
                    for x in range(ex - m, ex + m + 1):
                        if 0 <= x < PANEL_W and 0 <= y < PANEL_H:
                            grid[y][x] = True
        return grid

    def rows(self, margin: int = 1) -> List[str]:
        """Text map: '#' usable LED, 'E' eye hole, 'm' eye margin, '.' outside the oval."""
        eyes, blocked = self.eye_cells(), self.blocked(margin)
        out = []
        for y in range(PANEL_H):
            row = ""
            for x in range(PANEL_W):
                row += "." if not in_oval(x, y) else "E" if (x, y) in eyes else "m" if blocked[y][x] else "#"
            out.append(row)
        return out

    def to_json(self, margin: int = 1) -> dict:
        return {"width": PANEL_W, "height": PANEL_H, "source": self.source, "margin": margin,
                "oval": {"cx": OVAL_CX, "cy": OVAL_CY, "rx": OVAL_RX, "ry": OVAL_RY},
                "eyes": [{"bbox": e.get("bbox"), "center": e.get("center"), "cells": e["cells"]} for e in self.eyes],
                "rows": self.rows(margin), "legend": {"#": "usable", "E": "eye hole", "m": "eye margin",
                                                      ".": "outside oval"}}


@lru_cache(maxsize=4)
def default_panel(path: Optional[str] = None) -> PanelMap:
    return PanelMap.load(path)


def _lit_bbox(img: Image.Image, threshold: int):
    return img.convert("L").point(lambda v: 255 if v > threshold else 0).getbbox() if threshold < 255 else None


def _row_masks(img: Image.Image, x0: int, threshold: int) -> List[int]:
    """Per row, a bitmask (bit x) of lit pixels after shifting right by x0."""
    px, (w, h) = img.load(), img.size
    return [sum(1 << (x + x0) for x in range(w) if max(px[x, y]) > threshold) for y in range(h)]


def _blocked_masks(grid: Sequence[Sequence[bool]]) -> List[int]:
    return [sum(1 << x for x, b in enumerate(row) if b) for row in grid]


def _tops(span: int, valign: str) -> List[int]:
    tops = list(range(span + 1))
    if valign == "top":
        return tops
    if valign == "bottom":
        return tops[::-1]
    mid = span / 2
    return sorted(tops, key=lambda t: (abs(t - mid), t))


def place(src, fit: str = "contain", avoid_eyes: bool = True, margin: int = 1, valign: str = "center",
          threshold: int = 32, panel: Optional[PanelMap] = None) -> Image.Image:
    """Return a 46x58 RGB image with src's lit pixels kept off blocked cells.

    fit='contain': crop src to its lit bounding box, then pick the largest width w (aspect
    kept, centred horizontally) for which some top row keeps every lit pixel off blocked
    cells; valign picks which such row ('center' = closest to the vertical middle).
    fit='cover'/'stretch': fill the panel as fit_panel does, then black out blocked cells.
    In every mode any remaining light on a blocked cell is blacked out at the end.
    """
    from .image import fit_panel, load_image

    if valign not in VALIGN:
        raise ValueError(f"valign must be one of {', '.join(VALIGN)}")
    panel = panel or default_panel()
    grid = panel.blocked(margin, avoid_eyes)
    img = load_image(src)
    if fit == "contain":
        box = _lit_bbox(img, threshold)
        if box is None:
            return Image.new("RGB", (PANEL_W, PANEL_H))
        img = img.crop(box)
        blocked = _blocked_masks(grid)
        out = None
        for w in range(PANEL_W, 0, -1):
            h = max(1, round(img.height * w / img.width))
            if h > PANEL_H:
                continue
            scaled = img.resize((w, h), Image.LANCZOS) if (w, h) != img.size else img
            x0 = (PANEL_W - w) // 2
            lit = _row_masks(scaled, x0, threshold)
            for top in _tops(PANEL_H - h, valign):
                if not any(m & blocked[top + i] for i, m in enumerate(lit)):
                    out = Image.new("RGB", (PANEL_W, PANEL_H))
                    out.paste(scaled, (x0, top))
                    break
            if out:
                break
        if out is None:
            raise ValueError("image cannot be placed without covering blocked cells")
    elif fit in ("cover", "stretch"):
        out = fit_panel(img, fit)
    else:
        raise ValueError("fit must be contain, cover or stretch")
    px = out.load()
    for y in range(PANEL_H):
        for x in range(PANEL_W):
            if grid[y][x]:
                px[x, y] = (0, 0, 0)
    return out
