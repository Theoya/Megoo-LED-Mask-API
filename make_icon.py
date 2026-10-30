"""Turn an icon (transparent-background PNG/WebP/etc.) into a 44x16 mask image.

The mask's LEDs are roughly square, so the icon keeps its aspect ratio. A --size
above 16 makes it bigger by letting the top/bottom overflow the 16-row frame.

  python make_icon.py images/claude_symbol_source.webp out.png --size 20 --color ff4600
"""
import argparse

from PIL import Image, ImageFilter

MASK_W, MASK_H = 44, 16


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--size", type=int, default=16, help="icon size in LEDs (16 = full height; more crops top/bottom)")
    ap.add_argument("--color", default="ff4600", help="hex RGB; LEDs need saturated colours")
    ap.add_argument("--thicken", type=int, default=15, help="pixels to fatten thin strokes by before shrinking (0 = off)")
    ap.add_argument("--x", type=int, default=None, help="left column (default: centred)")
    a = ap.parse_args()

    alpha = Image.open(a.src).convert("RGBA").split()[3]
    if a.thicken:
        alpha = alpha.filter(ImageFilter.MaxFilter(a.thicken | 1))
    alpha = alpha.resize((a.size, a.size), Image.LANCZOS)

    color = tuple(int(a.color[i : i + 2], 16) for i in (0, 2, 4))
    icon = Image.composite(Image.new("RGB", alpha.size, color), Image.new("RGB", alpha.size), alpha)

    x = (MASK_W - a.size) // 2 if a.x is None else a.x
    y = (MASK_H - a.size) // 2  # negative when size > 16: crops evenly top and bottom
    canvas = Image.new("RGB", (MASK_W, MASK_H))
    canvas.paste(icon, (x, y))
    canvas.save(a.out)
    print(f"wrote {a.out} ({a.size}x{a.size} icon at x={x}, y={y})")


if __name__ == "__main__":
    main()
