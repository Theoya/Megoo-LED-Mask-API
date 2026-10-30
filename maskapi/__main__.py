"""CLI that drives the mask directly through the library (no HTTP server needed).

  python -m maskapi scan
  python -m maskapi diy face.png [--slot 1] [--fit contain] [--avoid-eyes] [--no-play] [--preview]
  python -m maskapi animation anim.gif [--start-slot 1]   (or several frame files / a frame dir)
  python -m maskapi play 1 2 3 | delete 4 5 | count       (play takes up to 20 slots)
  python -m maskapi shows [--order logo spiral ...] [--remove NAME]
  python -m maskapi define-show NAME frames... [--start-slot N] [--avoid-eyes] [--force]
  python -m maskapi play-show NAME | next | gesture shows
  python -m maskapi place in.png out.png [--margin 1] [--valign center] [--preview]   (no mask needed)
  python -m maskapi panel [--margin 1]                    (print the oval / eye map)
  python -m maskapi text "HELLO" [--color 00ff00] [--rainbow] [--preview]
  python -m maskapi image icon.png [--threshold 70] [--preview]      (16-row path)
  python -m maskapi brightness 40 | mode 3 | speed 5 | builtin-image 0 | builtin-anim 2 | loop
  python -m maskapi gesture on [--random] [--select diy --ids 1 2 3] | gesture off
  python -m maskapi raw LIGHT 60 [--wait 1] | rawhex 064c494748543c
  python -m maskapi serve [--host 0.0.0.0] [--fake]

Global: --address MAC, --fake (in-memory mask), --shows PATH, --eyes PATH, -v.
"""
import argparse
import asyncio
import logging
import sys

from . import protocol as P
from .client import Mask
from .image import collect_frames, encode_image, encode_panel, encode_pixels, render_text
from .panel import PanelMap, default_panel, place
from .shows import ShowRegistry


def add_place(p, default_avoid=False):
    if default_avoid:
        p.add_argument("--no-avoid-eyes", dest="avoid_eyes", action="store_false",
                       help="only keep light inside the oval")
    else:
        p.add_argument("--avoid-eyes", action="store_true", help="keep lit pixels off the eye holes and oval edge")
    p.add_argument("--margin", type=int, default=1, help="cells around each eye hole to keep dark")
    p.add_argument("--valign", choices=["center", "top", "bottom"], default="center")


def build_parser():
    ap = argparse.ArgumentParser(prog="python -m maskapi")
    ap.add_argument("--address")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--shows", help="shows registry JSON (default $MASKAPI_SHOWS or ~/.maskapi/shows.json)")
    ap.add_argument("--eyes", help="eyes.json (default: repo eyes.json)")
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
    add_place(p)

    p = sub.add_parser("animation")
    p.add_argument("frames", nargs="+", help="an animated GIF, several image files, or a frame directory")
    p.add_argument("--start-slot", type=int, default=1)
    p.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain")
    add_place(p)

    p = sub.add_parser("shows", help="list the shows registry")
    p.add_argument("--order", nargs="+", help="set the cycle order")
    p.add_argument("--remove", help="forget a show (slots are left as they are)")

    p = sub.add_parser("define-show", help="upload frames to slots and record them as a show")
    p.add_argument("name")
    p.add_argument("frames", nargs="+", help="images, an animated GIF, or a frame directory (<=20 frames)")
    p.add_argument("--start-slot", type=int, help="default: the show's slots, else the first free run")
    p.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain")
    p.add_argument("--force", action="store_true", help="overwrite slots of other shows (they are dropped)")
    p.add_argument("--no-play", action="store_true")
    add_place(p)

    sub.add_parser("play-show").add_argument("name")
    sub.add_parser("next", help="play the next show in order")

    p = sub.add_parser("place", help="eye-safe 46x58 placement of an image (no mask needed)")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain")
    p.add_argument("--preview", action="store_true", help="also print an ASCII preview")
    add_place(p, default_avoid=True)

    sub.add_parser("panel", help="print the oval / eye-hole map").add_argument("--margin", type=int, default=1)

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
    p.add_argument("state", choices=["on", "off", "shows"], help="shows: cycle the first slot of each show")
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

    def progress(i, n):
        print(f"  frame {i}/{n} uploaded", flush=True)

    panel_map = PanelMap.load(a.eyes) if a.eyes else None
    async with Mask(FakeTransport() if a.fake else None, address=a.address, shows=a.shows,
                    panel_map=panel_map) as m:
        c = a.cmd
        if c == "diy":
            await m.upload_diy(a.image, a.slot, a.fit, play=not a.no_play, avoid_eyes=a.avoid_eyes,
                               margin=a.margin, valign=a.valign)
        elif c == "animation":
            frames = collect_frames(a.frames, P.MAX_PLAY)
            print("slots", await m.upload_animation(frames, a.start_slot, a.fit, avoid_eyes=a.avoid_eyes,
                                                    margin=a.margin, valign=a.valign, progress=progress))
        elif c == "define-show":
            r = await m.define_show(a.name, a.frames, a.start_slot, a.fit, a.avoid_eyes, a.margin, a.valign,
                                    play=not a.no_play, force=a.force, progress=progress)
            print(f"show {r['name']}: slots {r['slots']}" + (f" (dropped {r['dropped']})" if r["dropped"] else ""))
        elif c == "play-show":
            print(a.name, await m.play_show(a.name))
        elif c == "next":
            name = await m.next_show()
            print(name, m.shows.slots(name))
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
        elif c == "gesture" and a.state == "shows":
            print("gesture ids", await m.gesture_our_shows(a.random))
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
    panel_map = PanelMap.load(a.eyes) if a.eyes else None
    if a.cmd == "shows":  # offline: the registry is a local file
        reg = ShowRegistry(a.shows)
        if a.order:
            reg.set_order(a.order)
        if a.remove:
            reg.remove(a.remove)
        d = reg.to_json()
        print(f"registry {d['path']}  current: {d['current']}")
        for n in d["order"]:
            sl = d["shows"][n]["slots"]
            print(f"  {n:12} slots {sl[0]}-{sl[-1]}" if len(sl) > 1 else f"  {n:12} slot {sl[0]}")
        return 0
    if a.cmd == "place":
        img = place(a.input, a.fit, a.avoid_eyes, a.margin, a.valign, panel=panel_map)
        img.save(a.output)
        box = img.getbbox()
        print(f"wrote {a.output} (lit area {box})")
        if a.preview:
            print("\n".join(encode_panel(img).preview()))
        return 0
    if a.cmd == "panel":
        pm = panel_map or default_panel()
        print(f"# {pm.source}  (# usable, E eye hole, m margin, . outside oval)")
        print("\n".join(pm.rows(a.margin)))
        return 0
    if getattr(a, "preview", False):
        if a.cmd == "diy":
            print("\n".join(encode_panel(a.image, a.fit, a.avoid_eyes, a.margin, a.valign, panel_map).preview()))
        elif a.cmd == "text":
            print("\n".join(encode_pixels(render_text(a.text, a.color, font_size=a.font_size), 0).preview))
        else:
            print("\n".join(encode_image(a.image, a.width, a.threshold).preview))
        return 0
    try:
        return asyncio.run(run(a))
    except ValueError as e:  # bad slot/show/frames: a message, not a traceback
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
