"""A tiny Claude Code session scrolling on the full panel, one line per frame.

Uses a 3x5 pixel font. The transcript loops seamlessly: with N lines and N frames,
each frame scrolls up exactly one line, so the last frame flows into the first.

  python claude_session.py images/session
"""
import argparse
import os

from PIL import Image

W, H = 46, 58
LH = 6  # line height: 5px glyph + 1px gap

# 3x5 glyphs, rows top->bottom, '#' = on
FONT = {
    "A": "###/#.#/###/#.#/#.#", "B": "##./#.#/##./#.#/##.", "C": "###/#../#../#../###",
    "D": "##./#.#/#.#/#.#/##.", "E": "###/#../##./#../###", "F": "###/#../##./#../#..",
    "G": "###/#../#.#/#.#/###", "H": "#.#/#.#/###/#.#/#.#", "I": "###/.#./.#./.#./###",
    "J": "..#/..#/..#/#.#/###", "K": "#.#/#.#/##./#.#/#.#", "L": "#../#../#../#../###",
    "M": "#.#/###/###/#.#/#.#", "N": "##./#.#/#.#/#.#/#.#", "O": "###/#.#/#.#/#.#/###",
    "P": "###/#.#/###/#../#..", "Q": "###/#.#/#.#/###/..#", "R": "###/#.#/##./#.#/#.#",
    "S": "###/#../###/..#/###", "T": "###/.#./.#./.#./.#.", "U": "#.#/#.#/#.#/#.#/###",
    "V": "#.#/#.#/#.#/#.#/.#.", "W": "#.#/#.#/###/###/#.#", "X": "#.#/#.#/.#./#.#/#.#",
    "Y": "#.#/#.#/.#./.#./.#.", "Z": "###/..#/.#./#../###",
    "0": "###/#.#/#.#/#.#/###", "1": ".#./##./.#./.#./###", "2": "###/..#/###/#../###",
    "3": "###/..#/###/..#/###", "4": "#.#/#.#/###/..#/..#", "5": "###/#../###/..#/###",
    "6": "###/#../###/#.#/###", "7": "###/..#/..#/..#/..#", "8": "###/#.#/###/#.#/###",
    "9": "###/#.#/###/..#/###", " ": ".../.../.../.../...", ".": ".../.../.../.../.#.",
    ">": "#../.#./..#/.#./#..", "+": ".../.#./###/.#./...", "-": ".../.../###/.../...",
    "*": "#.#/.#./###/.#./#.#", "_": ".../.../.../.../###", "/": "..#/..#/.#./#../#..",
    "(": ".#./#../#../#../.#.", ")": ".#./..#/..#/..#/.#.", ":": ".../.#./.../.#./...",
    "o": ".../.#./###/.#./...",  # filled bullet
}

ORANGE, WHITE, GREY = (255, 70, 0), (190, 190, 190), (110, 110, 110)
GREEN, RED, BLUE = (0, 200, 40), (220, 0, 0), (40, 90, 255)

# the looping transcript: list of [(text, colour), ...] segments per line
SESSION = [
    [("> ", GREY), ("FIX BUG", WHITE)],
    [("o ", ORANGE), ("READ", WHITE)],
    [("  MASK.PY", GREY)],
    [("o ", GREEN), ("EDIT", WHITE)],
    [("  +12", GREEN), (" -3", RED)],
    [("o ", BLUE), ("BASH", WHITE)],
    [("  TEST OK", GREEN)],
    [("* ", ORANGE), ("DONE", ORANGE)],
    [("", GREY)],
]


def draw_text(px, x, y, segments):
    for text, color in segments:
        for ch in text:
            glyph = FONT.get(ch, FONT[" "]).split("/")
            for gy, row in enumerate(glyph):
                for gx, c in enumerate(row):
                    if c == "#" and 0 <= x + gx < W and 0 <= y + gy < H:
                        px[x + gx, y + gy] = color
            x += 4


def in_oval(x, y):
    return ((x + 0.5 - W / 2) / 21.5) ** 2 + ((y + 0.5 - H / 2) / 27.0) ** 2 <= 1.0


def frame(k, top, bottom, x0):
    """Frame k: transcript scrolled up by k lines, shown between rows top..bottom."""
    img = Image.new("RGB", (W, H))
    px = img.load()
    n = len(SESSION)
    first = top - k * LH
    for rep in range(-1, 3):  # draw the transcript repeated so the window is always full
        for i, line in enumerate(SESSION):
            y = first + (rep * n + i) * LH
            if top - LH < y < bottom:
                draw_text(px, x0, y, line)
    # clip the scroll region, then add the fixed header and prompt
    for y in range(H):
        for x in range(W):
            if not (top <= y < bottom) or not in_oval(x, y):
                px[x, y] = (0, 0, 0)
    draw_text(px, 15, 4, [("* ", ORANGE), ("CC", ORANGE)])      # header: Claude Code star
    for x in range(8, 38):
        px[x, bottom + 1] = GREY                               # prompt box rule
    draw_text(px, 11, bottom + 3, [("> ", WHITE), ("_" if k % 2 == 0 else " ", ORANGE)])
    return img


TINY_SESSION = [  # longer transcript for --tiny (anti-aliased TrueType text, ~3px tall)
    ("> fix the bug", GREY),
    ("* Read mask.py", ORANGE),
    ("* Edit diy.py", GREEN),
    ("  +12 -3", GREEN),
    ("* Bash pytest", BLUE),
    ("  42 passed", GREEN),
    ("* Update README", GREEN),
    ("* Done", ORANGE),
    ("", GREY),
]


def tiny_frame(k, top, bottom, x0, font_path, px, lh, header=True):
    """Like frame(), but text is drawn with a real font at 8x and box-downsampled."""
    from PIL import ImageDraw, ImageFont

    ss = 8
    font = ImageFont.truetype(font_path, px * ss * 1.35)
    big = Image.new("RGB", (W * ss, H * ss))
    d = ImageDraw.Draw(big)
    n = len(TINY_SESSION)
    first = top - k * lh
    for rep in range(-1, 3):
        for i, (text, color) in enumerate(TINY_SESSION):
            y = first + (rep * n + i) * lh
            if top - lh < y < bottom:
                d.text((x0 * ss, y * ss), text, font=font, fill=color)
    img = big.resize((W, H), Image.BOX)
    pxs = img.load()
    for y in range(H):
        for x in range(W):
            if not (top <= y < bottom) or not in_oval(x, y):
                pxs[x, y] = (0, 0, 0)
    if header:
        draw_text(pxs, 15, 4, [("* ", ORANGE), ("CC", ORANGE)])
    for x in range(8, 38):
        pxs[x, bottom + 1] = GREY
    draw_text(pxs, 11, bottom + 3, [("> ", WHITE), ("_" if k % 2 == 0 else " ", ORANGE)])
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--top", type=int, default=11)
    ap.add_argument("--bottom", type=int, default=47)
    ap.add_argument("--x", type=int, default=6)
    ap.add_argument("--tiny", action="store_true", help="half-size anti-aliased text, more lines")
    ap.add_argument("--font", default=r"C:\Windows\Fonts\arialbd.ttf")
    ap.add_argument("--px", type=int, default=4, help="--tiny text height in LEDs")
    ap.add_argument("--no-header", action="store_true", help="drop the '* CC' header and scroll from the top")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    if a.tiny:
        top = 4 if a.no_header else a.top
        frames = [tiny_frame(k, top, a.bottom, 4, a.font, a.px, a.px + 1, not a.no_header) for k in range(len(TINY_SESSION))]
    else:
        frames = [frame(k, a.top, a.bottom, a.x) for k in range(len(SESSION))]
    for i, f in enumerate(frames):
        f.save(os.path.join(a.outdir, f"frame{i + 1:02d}.png"))
    sheet = Image.new("RGB", (W * 4 * len(frames), H * 4))
    for i, f in enumerate(frames):
        sheet.paste(f.resize((W * 4, H * 4), Image.NEAREST), (i * W * 4, 0))
    sheet.save(os.path.join(a.outdir, "sheet.png"))
    big = [f.resize((W * 6, H * 6), Image.NEAREST) for f in frames]
    big[0].save(os.path.join(a.outdir, "preview.gif"), save_all=True, append_images=big[1:], duration=1000, loop=0)
    print(f"wrote {len(frames)} frames to {a.outdir}")


if __name__ == "__main__":
    main()
