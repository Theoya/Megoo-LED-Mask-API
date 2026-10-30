"""CLI that drives the mask directly through the library (no HTTP server needed).

  python -m maskapi scan
  python -m maskapi diy face.png [--slot 1] [--fit contain] [--no-play] [--preview]
  python -m maskapi animation anim.gif [--start-slot 1]   (or several frame files)
  python -m maskapi play 1 2 3 | delete 4 5 | count
  python -m maskapi text "HELLO" [--color 00ff00] [--rainbow] [--preview]
  python -m maskapi image icon.png [--threshold 70] [--preview]      (16-row path)
  python -m maskapi brightness 40 | mode 3 | speed 5 | builtin-image 0 | builtin-anim 2 | loop
  python -m maskapi gesture on [--random] [--select diy --ids 1 2 3] | gesture off
  python -m maskapi raw LIGHT 60 [--wait 1] | rawhex 064c494748543c
  python -m maskapi serve [--host 0.0.0.0] [--fake]

Global: --address MAC, --fake (in-memory mask), -v.
"""
import argparse
import asyncio
import logging
import sys

from . import protocol as P
from .client import Mask
from .image import encode_image, encode_panel, encode_pixels, render_text


def build_parser():
    ap = argparse.ArgumentParser(prog="python -m maskapi")
    ap.add_argument("--address")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("count")
    sub.add_parser("loop")
    sub.add_parser("serve", add_help=False)

    p = sub.add_parser("diy")
    p.add_argument("image")
    p.add_argument("--slot", type=int, default=1)
    p.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain")
    p.add_argument("--no-play", action="store_true")
    p.add_argument("--preview", action="store_true", help="print ASCII preview, don't upload")

    p = sub.add_parser("animation")
    p.add_argument("frames", nargs="+", help="an animated GIF or several image files")
    p.add_argument("--start-slot", type=int, default=1)
    p.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain")

    for name in ("play", "delete"):
        sub.add_parser(name).add_argument("slots", nargs="+", type=int)

    p = sub.add_parser("text")
    p.add_argument("text")
    p.add_argument("--color", default="ff4600")
    p.add_argument("--rainbow", action="store_true")
    p.add_argument("--mode", type=int)
    p.add_argument("--speed", type=int)
    p.add_argument("--brightness", type=int)
    p.add_argument("--font-size", type=int)
    p.add_argument("--preview", action="store_true")

    p = sub.add_parser("image")
    p.add_argument("image")
    p.add_argument("--width", type=int, default=44)
    p.add_argument("--threshold", type=int, default=40)
    p.add_argument("--brightness", type=int)
    p.add_argument("--preview", action="store_true")

    for name in ("brightness", "mode", "speed", "builtin-image", "builtin-anim"):
        sub.add_parser(name).add_argument("value", type=int)

    p = sub.add_parser("gesture")
    p.add_argument("state", choices=["on", "off"])
    p.add_argument("--random", action="store_true")
    p.add_argument("--select", choices=["all", "builtin", "diy"], default="all")
    p.add_argument("--ids", type=int, nargs="*", default=[])

    p = sub.add_parser("raw")
    p.add_argument("name")
    p.add_argument("args", nargs="*", type=lambda s: int(s, 0))
    p.add_argument("--expect")
    p.add_argument("--wait", type=float, default=1.0)

    p = sub.add_parser("rawhex")
    p.add_argument("hex")
    p.add_argument("--expect")
    p.add_argument("--wait", type=float, default=1.0)
    return ap


async def run(a):
    if a.cmd == "scan":
        from .transport import scan

        hits = await scan(30.0)
        for dev, name, rssi in hits:
            print(f"{name} {dev.address} ({rssi} dBm)")
        if not hits:
            print("Mask not found.")
        return 0 if hits else 1

    from .fake import FakeTransport

    async with Mask(FakeTransport() if a.fake else None, address=a.address) as m:
        c = a.cmd
        if c == "diy":
            await m.upload_diy(a.image, a.slot, a.fit, play=not a.no_play)
        elif c == "animation":
            frames = a.frames[0] if len(a.frames) == 1 else a.frames
            print("slots", await m.upload_animation(frames, a.start_slot, a.fit))
        elif c == "play":
            await m.play_slots(a.slots)
        elif c == "delete":
            await m.delete_slots(a.slots)
        elif c == "count":
            print(await m.diy_count())
        elif c == "loop":
            await m.loop_builtin_animations()
        elif c == "text":
            f = await m.show_text(a.text, a.color, a.mode, a.speed, a.brightness,
                                  font_size=a.font_size, rainbow=a.rainbow)
            print(f"{f.width} columns")
        elif c == "image":
            await m.upload_image(a.image, a.width, a.threshold, a.brightness)
        elif c == "brightness":
            await m.set_brightness(a.value)
        elif c == "mode":
            await m.set_mode(a.value)
        elif c == "speed":
            await m.set_speed(a.value)
        elif c == "builtin-image":
            await m.show_builtin_image(a.value)
        elif c == "builtin-anim":
            await m.show_builtin_animation(a.value)
        elif c == "gesture":
            sel = {"all": P.SELECT_ALL, "builtin": P.SELECT_BUILTIN, "diy": P.SELECT_DIY}[a.select]
            await m.set_gesture(a.state == "on", a.random, sel, a.ids)
        elif c == "raw":
            print(await m.send_command(a.name.upper(), *a.args, expect=a.expect, wait=a.wait))
        elif c == "rawhex":
            print(await m.send_raw(bytes.fromhex(a.hex), expect=a.expect, wait=a.wait))
        await asyncio.sleep(0.3)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "serve":
        from .server import main as serve

        return serve(argv[1:])
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING)
    if getattr(a, "preview", False):
        if a.cmd == "diy":
            print("\n".join(encode_panel(a.image, a.fit).preview()))
        elif a.cmd == "text":
            print("\n".join(encode_pixels(render_text(a.text, a.color, font_size=a.font_size), 0).preview))
        else:
            print("\n".join(encode_image(a.image, a.width, a.threshold).preview))
        return 0
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
