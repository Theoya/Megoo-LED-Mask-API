"""Image/text encoding tests for the 16-row path and the 46x58 DIY panel."""
from PIL import Image

from maskapi import image as I
from maskapi.protocol import PANEL_H, PANEL_W


def test_row_bits():
    assert I.row_bit(0) == 0x80 and I.row_bit(7) == 0x01
    assert I.row_bit(8) == 0x8000 and I.row_bit(15) == 0x0100


def test_encode_pixels_bitmap_and_colour():
    img = Image.new("RGB", (3, 16))
    img.putpixel((0, 0), (255, 0, 0))
    img.putpixel((0, 15), (128, 0, 0))
    img.putpixel((2, 8), (0, 100, 50))
    f = I.encode_pixels(img)
    assert f.width == 3 and len(f.payload) == 3 * 5
    assert f.bitmap == bytes([0x80, 0x01, 0, 0, 0x00, 0x80])
    assert f.colours[:3] == bytes([255, 0, 0])  # avg (191,0,0) normalised to peak
    assert f.colours[3:6] == b"\x00\x00\x00"
    assert f.colours[6:9] == bytes([0, 255, 127])
    assert f.preview[0][0] == "#" and f.preview[8] == "  #"


def test_encode_image_matches_mask_upload(tmp_path):
    src = Image.new("RGBA", (88, 32), (0, 0, 0, 0))
    for x in range(20, 60):
        for y in range(4, 28):
            src.putpixel((x, y), (255, 70, 0, 255))
    path = tmp_path / "icon.png"
    src.save(path)
    f = I.encode_image(str(path))
    assert f.width == 44 and len(f.bitmap) == 88 and len(f.colours) == 132
    lit = [i for i in range(44) if f.bitmap[2 * i] or f.bitmap[2 * i + 1]]
    assert lit and min(lit) >= 9 and max(lit) <= 31
    assert f.colours[3 * lit[5] : 3 * lit[5] + 3][0] == 255
    assert I.encode_image(src, width=0).width == 44  # aspect kept: 88x32 -> 44x16


def test_render_text():
    img = I.render_text("HI", "00ff00")
    assert img.height == 16 and img.width == 44
    colours = {c for _, c in img.getcolors()}
    assert colours <= {(0, 0, 0), (0, 255, 0)} and (0, 255, 0) in colours
    long = I.render_text("HELLO WORLD THIS SCROLLS")
    assert long.width > 44
    rb = I.render_text("RAINBOW", rainbow=True)
    assert len(rb.getcolors(10000)) > 3


def test_panel_column_major():
    img = Image.new("RGB", (PANEL_W, PANEL_H))
    img.putpixel((0, 1), (1, 2, 3))
    img.putpixel((1, 0), (4, 5, 6))
    img.putpixel((45, 57), (7, 8, 9))
    p = I.encode_panel(img)
    data = p.payload
    assert len(data) == 8004
    assert data[3:6] == bytes([1, 2, 3])
    assert data[PANEL_H * 3 : PANEL_H * 3 + 3] == bytes([4, 5, 6])
    assert data[-3:] == bytes([7, 8, 9])


def test_panel_fit_modes():
    wide = Image.new("RGB", (200, 100), (255, 0, 0))
    c = I.fit_panel(wide, "contain")
    assert c.size == (PANEL_W, PANEL_H)
    assert c.getpixel((23, 0)) == (0, 0, 0) and c.getpixel((23, 29)) == (255, 0, 0)  # letterboxed
    assert I.fit_panel(wide, "cover").getpixel((23, 0)) == (255, 0, 0)
    assert I.fit_panel(wide, "stretch").getpixel((0, 0)) == (255, 0, 0)
    transparent = Image.new("RGBA", (10, 10), (255, 255, 255, 0))
    assert I.encode_panel(transparent).payload == bytes(8004)  # alpha -> black


def test_load_frames_gif(tmp_path):
    frames = [Image.new("RGB", (20, 20), (i * 20, 0, 0)) for i in range(12)]
    path = tmp_path / "a.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100)
    assert len(I.load_frames(str(path))) == 12
    assert len(I.load_frames(path.read_bytes(), max_frames=5)) == 5
