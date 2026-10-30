"""Image and text -> mask payloads.

16-row text/bitmap path: per column a uint16 LE (rows 0-7 set bits 0x80>>row of the
low byte, rows 8-15 set 0x8000>>(row-8)), then 3 bytes RGB per column.
Full-panel DIY path: 46x58 RGB888, column-major (x=0 first, each column top->bottom).
"""
from __future__ import annotations

import colorsys
import io
import os
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple, Union

from PIL import Image, ImageDraw, ImageFont, ImageSequence

from .protocol import PANEL_H, PANEL_W, ROWS

MASK_WIDTH = 44
RGB = Tuple[int, int, int]
ImageSource = Union[str, os.PathLike, bytes, Image.Image]
FONT_CANDIDATES = (
    "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "arialbd.ttf",
)


@dataclass
class Frame:
    bitmap: bytes
    colours: bytes
    preview: list

    @property
    def width(self) -> int:
        return len(self.bitmap) // 2

    @property
    def payload(self) -> bytes:
        return self.bitmap + self.colours


def row_bit(y: int) -> int:
    return (0x80 >> y) if y < 8 else (0x8000 >> (y - 8))


def parse_color(c: Union[str, Sequence[int]]) -> RGB:
    if isinstance(c, str):
        c = c.lstrip("#")
        if len(c) != 6:
            raise ValueError(f"colour must be 6 hex digits, got {c!r}")
        return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))
    r, g, b = c
    return int(r), int(g), int(b)


def load_image(src: ImageSource) -> Image.Image:
    """Open a path/bytes/PIL image and flatten any alpha onto black."""
    if isinstance(src, Image.Image):
        img = src
    elif isinstance(src, (bytes, bytearray)):
        img = Image.open(io.BytesIO(src))
    else:
        img = Image.open(src)
    img = img.convert("RGBA")
    return Image.alpha_composite(Image.new("RGBA", img.size, (0, 0, 0, 255)), img).convert("RGB")


def encode_columns(columns: Iterable[Tuple[int, Optional[RGB]]]) -> Frame:
    """Build a Frame from (bitmask, colour) per column; colour None -> black."""
    bitmap, colours, cols = bytearray(), bytearray(), []
    for val, colour in columns:
        bitmap += val.to_bytes(2, "little")
        colours += bytes(colour or (0, 0, 0))
        cols.append(val)
    preview = ["".join("#" if v & row_bit(y) else " " for v in cols) for y in range(ROWS)]
    return Frame(bytes(bitmap), bytes(colours), preview)


def encode_pixels(img: Image.Image, threshold: int = 40) -> Frame:
    """Encode an RGB image that is already ROWS tall. One colour per column:
    the brightest-normalised average of that column's lit pixels."""
    if img.height != ROWS:
        raise ValueError(f"image must be {ROWS} rows tall, got {img.height}")
    img = img.convert("RGB")
    px = img.load()
    cols = []
    for x in range(img.width):
        val, lit = 0, []
        for y in range(ROWS):
            r, g, b = px[x, y]
            if max(r, g, b) > threshold:
                val |= row_bit(y)
                lit.append((r, g, b))
        colour = None
        if lit:
            avg = [sum(c[i] for c in lit) // len(lit) for i in range(3)]
            peak = max(avg) or 1
            colour = tuple(min(255, v * 255 // peak) for v in avg)
        cols.append((val, colour))
    return encode_columns(cols)


def encode_image(src: ImageSource, width: Optional[int] = MASK_WIDTH, threshold: int = 40) -> Frame:
    """Resize to width x 16 (width None/0 keeps aspect ratio) and encode."""
    img = load_image(src)
    if not width:
        width = max(1, round(img.width * ROWS / img.height))
    if img.size != (width, ROWS):
        img = img.resize((width, ROWS), Image.LANCZOS)
    return encode_pixels(img, threshold)


def load_font(size: int = 16, path: Optional[str] = None):
    for cand in ([path] if path else []) + list(FONT_CANDIDATES):
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            if cand == path:
                raise
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def fit_font(text: str, path: Optional[str] = None, size: Optional[int] = None):
    """Largest font whose ink for the text fits in ROWS (or the given size)."""
    if size:
        return load_font(size, path)
    for s in range(22, 5, -1):
        font = load_font(s, path)
        l, t, r, b = font.getbbox(text or "H")
        if b - t <= ROWS:
            return font
        if not hasattr(font, "size"):  # fixed bitmap font, can't scale
            return font
    return font


def render_text(text: str, color: Union[str, RGB] = "ff4600", font_path: Optional[str] = None,
                font_size: Optional[int] = None, min_width: int = MASK_WIDTH, spacing: int = 1,
                rainbow: bool = False) -> Image.Image:
    """Render text into a black ROWS-tall image, no anti-aliasing, centred vertically.
    Narrow text is centred within min_width columns."""
    font = fit_font(text, font_path, font_size)
    l, t, r, b = font.getbbox(text) if text else (0, 0, 0, 0)
    w = max(min_width, r - l + 2 * spacing)
    img = Image.new("RGB", (w, ROWS))
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    x = (w - (r - l)) // 2 - l
    y = (ROWS - (b - t)) // 2 - t
    d.text((x, y), text, fill=(255, 255, 255), font=font)
    if rainbow:
        px = img.load()
        for cx in range(w):
            col = _hue(cx / max(1, w))
            for cy in range(ROWS):
                if px[cx, cy] != (0, 0, 0):
                    px[cx, cy] = col
    else:
        tint = Image.new("RGB", img.size, parse_color(color))
        img = Image.composite(tint, img, img.convert("L").point(lambda v: 255 if v > 127 else 0))
    return img


def _hue(h: float) -> RGB:
    return tuple(int(v * 255) for v in colorsys.hsv_to_rgb(h, 1, 1))


# ---- full-panel DIY ------------------------------------------------------------------

@dataclass
class Panel:
    image: Image.Image  # PANEL_W x PANEL_H RGB, as sent

    @property
    def payload(self) -> bytes:
        return self.image.transpose(getattr(Image, "Transpose", Image).TRANSPOSE).tobytes()

    def preview(self, threshold: int = 40) -> list:
        px = self.image.load()
        return ["".join("#" if max(px[x, y]) > threshold else " " for x in range(PANEL_W))
                for y in range(PANEL_H)]


def fit_panel(img: Image.Image, fit: str = "contain") -> Image.Image:
    """contain: keep aspect, black bars; cover: keep aspect, crop; stretch: ignore aspect."""
    img = load_image(img)
    size = (PANEL_W, PANEL_H)
    if fit == "stretch" or img.size == size:
        return img.resize(size, Image.LANCZOS) if img.size != size else img
    scale = (max if fit == "cover" else min)(PANEL_W / img.width, PANEL_H / img.height)
    if fit not in ("contain", "cover"):
        raise ValueError("fit must be contain, cover or stretch")
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    img = img.resize((w, h), Image.LANCZOS)
    canvas = Image.new("RGB", size)
    canvas.paste(img, ((PANEL_W - w) // 2, (PANEL_H - h) // 2))
    return canvas


def encode_panel(src: ImageSource, fit: str = "contain") -> Panel:
    return Panel(fit_panel(load_image(src), fit))


def load_frames(src: ImageSource, max_frames: Optional[int] = None) -> list:
    """All frames of an (animated) image as RGB, evenly subsampled to max_frames."""
    img = src if isinstance(src, Image.Image) else (
        Image.open(io.BytesIO(src)) if isinstance(src, (bytes, bytearray)) else Image.open(src))
    frames = [load_image(f.copy()) for f in ImageSequence.Iterator(img)]
    if max_frames and len(frames) > max_frames:
        step = len(frames) / max_frames
        frames = [frames[int(i * step)] for i in range(max_frames)]
    return frames
