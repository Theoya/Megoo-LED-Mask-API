# maskapi: library, CLI and HTTP API

`maskapi/` drives the Megoo "Shining Mask" over BLE. Run it on the Jetson, because that is the
only Bluetooth radio that reliably reaches the mask. The HTTP server holds the **single** BLE
connection, so only one process may talk to the mask at a time. Stop the server before you
run `mask_upload.py` / `probe.py`, and the reverse.

## Install (Jetson)

```
cd ~/LED-Mask
pip install --user -r requirements.txt      # adds fastapi, uvicorn, python-multipart
python3 -m pytest -q tests                  # optional; uses a fake mask, no Bluetooth
python3 -m maskapi serve                    # http://127.0.0.1:8765/docs
python3 -m maskapi serve --host 0.0.0.0 --address 5F:F6:A0:B0:42:F1
```

`serve` options: `--host` (default `127.0.0.1`), `--port` (8765), `--address` (skip the
name scan), `--connect` (connect at startup, not on the first request), `--idle-disconnect N`
(drop the link after N idle seconds, so the phone app can connect), `--fake` (in-memory
mask for development), `--log-level`.

The API has **no authentication**. Bind it to `0.0.0.0` only behind the Jetson firewall
(LAN/WireGuard). There is an example systemd user unit at `docs/maskapi.service`.

## Display model

| Path | Size | Colour | Commands |
|---|---|---|---|
| **DIY panel** (main) | 46 x 58 | RGB888 per pixel | `DATS .. 00 slot 01`, chunks, `DATCP <u32 time>`, `PLAY` |
| 16-row text/bitmap | N x 16 (44 visible) | one colour per column | `DATS total bitmap_len 00`, chunks, `DATCP`, `MODE` |

The mask stores 20 DIY slots. `PLAY` with several slots makes the mask cycle through
them, which is how `/animation` works. You can't set the per-frame timing; `SPEED` may
affect it (untested).

## Library

```python
import asyncio
from maskapi import Mask

async def main():
    async with Mask(address="5F:F6:A0:B0:42:F1") as m:   # or Mask() to scan for MASK*
        await m.upload_diy("face.png", slot=1, fit="contain")    # shown immediately
        await m.upload_animation("blink.gif", start_slot=2)      # slots 2.. then PLAY
        await m.play_slots([1, 2, 3])
        print(await m.diy_count())
        await m.delete_slots([3])
        await m.show_text("HELLO", "00ff00")                     # 16-row path, scrolls if > 44 wide
        await m.upload_image("icon.png", threshold=70)           # 16-row path (mask_upload.py logic)
        await m.set_brightness(40)
        await m.show_builtin_image(0); await m.show_builtin_animation(2)
        await m.loop_builtin_animations()
        await m.set_gesture(True, loop_random=False, select=2, ids=[1, 2, 3])
        await m.send_rhythm(1, [9, 7, 5] + [0] * 21)
        # reverse-engineering hooks
        await m.send_command("LIGHT", 60, wait=0.5)               # -> notifications list
        await m.send_raw(bytes.fromhex("064c494748543c"), expect="LIGHT")
        await m.upload(payload, bitmap_len, flag=0, after=[("MODE", 1)])
        await m.upload_generic(payload, dats_plain, datcp_plain)
        await m.write_char(uuid, data)

asyncio.run(main())
```

All public methods share one asyncio lock, so operations never interleave. Each one
connects if needed. On a `TransportError` (any bleak/GATT failure) or an `AckTimeout`,
it drops the link, reconnects and retries the **whole** operation (3 tries). Every
connect also gets 3 attempts with a 30 s timeout. A notification of `ERROR` raises
`MaskError` and is not retried.

The pure parts are separate modules: `maskapi.protocol` (packet layouts, AES) and
`maskapi.image` (`encode_panel`, `encode_image`, `render_text`, `load_frames`). Both are
usable without a mask. `maskapi.fake.FakeTransport` simulates the mask's acks.

## CLI

It calls the library directly; no server is needed. Stop the server first, because the
CLI makes its own connection.

```
python3 -m maskapi scan
python3 -m maskapi diy face.png --slot 1 [--fit cover] [--no-play] [--preview]
python3 -m maskapi animation blink.gif [--start-slot 1]        # or several frame files
python3 -m maskapi play 1 2 3 | delete 3 | count | loop
python3 -m maskapi text "HELLO" --color 00ff00 [--rainbow] [--preview]
python3 -m maskapi image icon.png --threshold 70 [--preview]
python3 -m maskapi brightness 40 | mode 3 | speed 5 | builtin-image 0 | builtin-anim 2
python3 -m maskapi gesture on --select diy --ids 1 2 3 | gesture off
python3 -m maskapi raw LIGHT 60 --wait 1 | rawhex 064c494748543c
python3 -m maskapi --fake diy face.png       # global flags go before the subcommand
```

## HTTP endpoints

Interactive docs are at `/docs` and the schema at `/openapi.json`. Errors come back as
`{"detail": ...}` with these status codes: 400 for a bad value or unreadable image, 422
for validation, 503 when the link failed after retries, and 502 when the mask replied `ERROR`.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/status` | | connected, name/address, last state set, last 10 notifications and sent commands, last_error |
| POST | `/connect`, `/disconnect` | | |
| POST | `/diy` | multipart `file` + `slot,fit,play,dry_run`, or JSON `image_base64,...` | image -> 46x58 (`contain` keeps aspect on black; `cover`, `stretch`), PLAYs it by default. Returns a PNG preview |
| POST | `/animation` | multipart `file` (one GIF or several images), or JSON `frames_base64: []` + `start_slot,fit,play,max_frames` | at most 10 frames (one PLAY packet); a longer GIF is subsampled to `max_frames` |
| POST | `/diy/play` | `{"slots":[1,2]}` | 1-10 slots |
| POST | `/diy/{slot}/play` | | |
| DELETE | `/diy` | `{"slots":[3]}` | sent in groups of 10 |
| GET | `/diy/count` | | `CHEC` -> `{"count": n}` |
| POST | `/text` | `{"text","color","rainbow","mode","speed","brightness","font_size","dry_run"}` | 16-row path |
| POST | `/image` | multipart or JSON, `width,threshold,brightness,mode,dry_run` | 16-row path, one colour per column |
| POST | `/builtin/image/{idx}` | optional `{"extra":[..]}` | `IMAG idx`, 0-based |
| POST | `/builtin/animation/{idx}` | optional `{"extra":[..]}` | `ANIM idx`, 0-based |
| POST | `/builtin/loop` | | `LOOA` |
| POST | `/gesture` | `{"enable","loop":"order|random","select":"all|builtin|diy","ids":[]}` | `FACE`, up to 7 ids |
| POST | `/brightness` | `{"level":40}` | `LIGHT`, 1-255 |
| POST | `/mode` | `{"mode":3}` | 1 steady, 2 blink, 3 scroll L, 4 scroll R, 5 steady |
| POST | `/speed` | `{"speed":5}` | |
| POST | `/color/foreground`, `/color/background` | `{"color":"ff0000","enable":true}` | `FC` / `BC` |
| POST | `/text-mode` | `{"enable":true,"mode":1}` | `M` |
| POST | `/rhythm` | `{"mode":0-4,"bars":[0-9 x <=24]}` | one frame to `...960b`; post every ~120 ms to stream |
| POST | `/raw` | `{"name":"LIGHT","args":[60]}` or `{"hex":"064c494748543c"}`, plus `char:"cmd|music"`, `expect`, `wait` | returns the plaintext sent and the notifications received |
| POST | `/upload` | `{"payload_hex"|"payload_base64", "kind":"text|diy", "bitmap_len","flag","slot","dats_hex","datcp_hex","after":[["MODE",1]]}` | generic upload for reverse-engineering |

### curl

```
H=http://127.0.0.1:8765
curl $H/status
curl -F file=@face.png -F slot=1 $H/diy
curl -F file=@face.png -F fit=cover -F dry_run=true $H/diy     # preview only
curl -F file=@blink.gif -F start_slot=5 $H/animation
curl -F file=@f1.png -F file=@f2.png -F file=@f3.png $H/animation
curl -H 'content-type: application/json' -d '{"slots":[1,5,6]}' $H/diy/play
curl -X DELETE -H 'content-type: application/json' -d '{"slots":[6]}' $H/diy
curl $H/diy/count
curl -H 'content-type: application/json' -d '{"text":"HELLO","color":"00ff00"}' $H/text
curl -F file=@icon.png -F threshold=70 $H/image
curl -X POST $H/builtin/image/0
curl -X POST $H/builtin/loop
curl -H 'content-type: application/json' -d '{"enable":true,"select":"diy","ids":[1,2,3]}' $H/gesture
curl -H 'content-type: application/json' -d '{"level":40}' $H/brightness
curl -H 'content-type: application/json' -d '{"name":"CHEC","expect":"CHEC"}' $H/raw
curl -H 'content-type: application/json' -d "{\"image_base64\":\"$(base64 -w0 face.png)\",\"slot\":2}" $H/diy
```

## What is verified

| Feature | Status |
|---|---|
| AES framing, notifications, 16-row DATS/REOK/DATCP upload, `MODE 1` | verified on the mask |
| Connect retry, weak signal | verified (`mask_upload.py`) |
| DIY 46x58 upload, `DATCP`+time, `PLAY`, `DELE`, `CHEC` | from decompiled official app 1.2.3; **not yet run on the mask by this code** |
| `IMAG`/`ANIM` (0-based), `LOOA`, `LIGHT`, `SPEED`, `FC`/`BC`, `M` layouts | from decompiled app; untested here |
| `FACE` gesture, rhythm frames on `...960b` | from decompiled app; untested |
| Acks `PLAYOK`/`DELEOK`/`FACEOK`/`CHEC n` | from the app. If the mask doesn't send them, those calls time out and retry; use `/raw` with `wait` to see what it really sends |
| Which DIY colour order (RGB vs GRB) and orientation the panel uses | unverified; check with an asymmetric test image |

Everything here is tested only against `FakeTransport` (`python -m pytest tests`).
