"""Eye-safe placement: oval + eye-hole map from eyes.json."""
import json

import pytest
from PIL import Image

from maskapi.panel import PanelMap, default_panel, in_oval, place
from maskapi.protocol import PANEL_H, PANEL_W


def lit_on_blocked(img, pm, margin=1):
    grid = pm.blocked(margin)
    px = img.load()
    return [(x, y) for y in range(PANEL_H) for x in range(PANEL_W) if grid[y][x] and max(px[x, y]) > 0]


def width(img):
    box = img.getbbox()
    return box[2] - box[0]


def test_default_map_matches_measurement():
    pm = default_panel()
    assert [e["bbox"] for e in pm.eyes] == [[5, 14, 17, 22], [28, 14, 40, 20]]
    assert not in_oval(0, 0) and in_oval(23, 29)
    rows = pm.rows(1)
    assert len(rows) == PANEL_H and all(len(r) == PANEL_W for r in rows)
    assert rows[17][10] == "E" and rows[0][0] == "." and rows[40][23] == "#"
    j = pm.to_json(2)
    assert j["margin"] == 2 and len(j["eyes"]) == 2 and j["oval"]["rx"] == 21.5


def test_place_square_avoids_eyes_and_oval():
    pm = default_panel()
    sq = Image.new("RGB", (100, 100), (255, 0, 0))
    img = place(sq, panel=pm)
    assert img.size == (PANEL_W, PANEL_H)
    assert lit_on_blocked(img, pm) == []
    assert width(img) >= 10
    assert width(place(sq, panel=pm, margin=0)) >= width(img)  # smaller margin never shrinks it


def test_place_is_largest_width():
    pm = default_panel()
    sq = Image.new("RGB", (100, 100), (255, 0, 0))
    w = width(place(sq, panel=pm))
    # a square one pixel wider, centred, fits at no top row
    w2 = w + 1
    x0 = (PANEL_W - w2) // 2
    grid = pm.blocked(1)
    fits = any(all(not grid[t + y][x0 + x] for y in range(w2) for x in range(w2))
               for t in range(PANEL_H - w2 + 1))
    assert not fits


def test_place_valign_and_crop():
    pm = default_panel()
    src = Image.new("RGB", (200, 40))
    src.paste((0, 255, 0), (50, 10, 150, 30))  # black border is cropped away first
    top = place(src, valign="top", panel=pm).getbbox()
    bottom = place(src, valign="bottom", panel=pm).getbbox()
    assert top[1] < bottom[1]
    assert top[2] - top[0] == bottom[2] - bottom[0]
    assert place(Image.new("RGB", (5, 5)), panel=pm).getbbox() is None  # all black stays black
    with pytest.raises(ValueError):
        place(src, valign="middle", panel=pm)


def test_place_cover_blacks_out_blocked():
    pm = default_panel()
    img = place(Image.new("RGB", (46, 58), (9, 200, 9)), fit="cover", panel=pm)
    assert lit_on_blocked(img, pm) == [] and img.getpixel((23, 40)) == (9, 200, 9)
    img = place(Image.new("RGB", (46, 58), (9, 200, 9)), fit="stretch", avoid_eyes=False, panel=pm)
    assert img.getpixel((10, 17)) != (0, 0, 0) and img.getpixel((0, 0)) == (0, 0, 0)


def test_custom_eyes_file(tmp_path):
    p = tmp_path / "eyes.json"
    p.write_text(json.dumps({"width": 46, "height": 58, "eyes": [{"cells": [[20, 20]]}]}))
    pm = PanelMap.load(str(p))
    assert pm.eye_cells() == {(20, 20)}
    assert sum(r.count("m") for r in pm.rows(1)) == 8
    p.write_text(json.dumps({"width": 10, "height": 10, "eyes": []}))
    with pytest.raises(ValueError):
        PanelMap.load(str(p))
