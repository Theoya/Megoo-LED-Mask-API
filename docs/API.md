# maskapi: library, CLI and HTTP API

`maskapi/` drives the Megoo "Shining Mask" over BLE. Run it on the Jetson, because that is the
only Bluetooth radio that reliably reaches the mask. The HTTP server holds the **single** BLE
connection, so only one process may talk to the mask at a time. Stop the server before you
run `diy.py` / `probe.py` / the CLI, and the reverse.

## Install (Jetson)

```
cd ~/Megoo-LED-Mask-API
pip install --user -r requirements.txt      # adds fastapi, uvicorn, python-multipart, websockets
python3 -m pytest -q tests                  # optional; uses a fake mask, no Bluetooth
python3 -m maskapi serve                    # http://127.0.0.1:8765/docs
python3 -m maskapi serve --host 0.0.0.0 --address 5F:F6:A0:B0:42:F1
```

`serve` options: `--host` (default `127.0.0.1`), `--port` (8765), `--address` (skip the
name scan), `--connect` (connect at startup), `--idle-disconnect N` (drop the link after N
idle seconds, so the phone app can connect), `--fake` (in-memory mask), `--shows PATH`,
`--eyes PATH`, the `/ws` limits (`--scratch-slot`, `--rhythm-interval`, `--play-interval`,
`--image-interval`, `--band-interval`, `--stats-interval`), `--log-level`.

The API has **no authentication**. Bind it to `0.0.0.0` only behind the Jetson firewall
(LAN/WireGuard). There is an example systemd user unit at `docs/maskapi.service`.

## Display model

| Path | Size | Colour | Commands |
|---|---|---|---|
| **DIY panel** (main) | 46 x 58, oval | RGB888 per pixel, column-major | `DATS .. 00 slot 01`, chunks, `DATCP <u32 time>`, `PLAY` |
| 16-row text/bitmap | N x 16 (44 visible) | one colour per column | `DATS total bitmap_len 00`, chunks, `DATCP`, `MODE` |

The mask stores 20 DIY slots. `PLAY` with several slots (up to 20) makes the mask cycle
them at ~1 frame/s with no crossfade. `SPEED` only slows that (1 is ~3.5 s/frame).

**Shows** are named slot lists kept in a JSON registry (`--shows`, `$MASKAPI_SHOWS`, else
`~/.maskapi/shows.json`). The default is the layout on the mask now: `logo` [1],
`spiral` [2-10], `virgil` [11], `session` [12-20], cycled in the order logo, spiral,
session, virgil. `next` plays the show after the current one and saves which is current.
Defining a show whose slots overlap another show is an error unless you pass `force`; the
overlapped shows are then dropped from the registry.

**Eye-safe placement** (`avoid_eyes`): the panel map is the oval plus the eye-hole cells in
`eyes.json` (from `find_eyes.py`; `--eyes`, `$MASKAPI_EYES`, `<repo>/eyes.json`, else the
copy in `maskapi/`). With `fit=contain` the image is cropped to its lit area, then the
largest width is chosen (aspect kept, centred horizontally) for which some top row keeps
every lit pixel off the outside of the oval and the eye cells + `margin`. `valign`
(`center`/`top`/`bottom`) picks among the rows that fit. With `cover`/`stretch` the image
fills the panel and the blocked cells are blacked out.

## Library

```python
import asyncio
from maskapi import Mask

async def main():
    async with Mask(address="5F:F6:A0:B0:42:F1") as m:           # or Mask() to scan for MASK*
        await m.upload_diy("face.png", slot=1, avoid_eyes=True)  # DELE, upload, PLAY
        await m.upload_animation("blink.gif", start_slot=2)      # up to 20 frames, then PLAY
        await m.play_slots(range(1, 21))                         # long PLAY form
        await m.define_show("tendrils", "images/tendrils", start_slot=11, force=True)
        await m.play_show("logo"); await m.next_show(); print(m.list_shows())
        await m.gesture_our_shows()                              # FACE over each show's first slot
        print(await m.diy_count())                               # CHEC
        await m.delete_slots([3])
        await m.show_text("HELLO", "00ff00", font_size=8)        # 16-row band
        await m.set_brightness(40)
        await m.show_builtin_image(1, 0)                         # 06 IMAG 01 00
        await m.set_gesture(True, select=2, ids=[1, 2, 12, 11])
        await m.send_rhythm(1, [9, 7, 5] + [0] * 21)
        await m.send_command("LIGHT", 60, wait=0.5)              # raw hooks: send_raw, upload_generic, write_char

asyncio.run(main())
```

`maskapi.panel.place(img, fit="contain", avoid_eyes=True, margin=1, valign="center")`
returns the 46x58 image without a mask. `maskapi.shows.ShowRegistry` is the registry on
its own.

**Retries.** All public methods share one asyncio lock. On a `TransportError` or a missing
ack, an operation is retried up to 4 times with the recipe that worked on the mask: drain
the notification queue, wait 1.5 s, reconnect only if the link actually dropped. Multi-frame
uploads retry each frame on its own. Uploads send `DELE` for the target slots first (one
attempt, failure ignored). A notification of `ERROR` raises `MaskError` and is not retried.

## Streaming (`/ws`, `maskapi.stream.Streamer`)

One WebSocket per client; JSON text frames, or a binary frame holding a PNG (= `image`).
Each lane keeps only the newest pending message: while a send is in flight, newer messages
replace each other (counted as `dropped`) and only the latest goes out next.

| Message | Lane | What is sent |
|---|---|---|
| `{"type":"slot","slot":n}`, `{"type":"show","name":...}` | play | `PLAY`, without waiting for `PLAYOK` |
| `{"type":"rhythm","mode":0-4,"bars":[24 x 0-9]}` | rhythm | `0F mode` + 12 nibble-packed bytes to `...960b`, at most every `rhythm_interval` |
| `{"type":"image","png_b64":...}` or binary PNG | image | upload to `scratch_slot` (default 20), then `PLAY` it. Optional `fit`, `avoid_eyes`, `margin`, `valign` |
| `{"type":"band","png_b64":...}` | band | 16-row upload + `MODE 1`. Optional `width` (0 = keep aspect), `threshold` |
| `{"type":"stats"}` | | replies with stats now |

Replies: `{"type":"hello","config":...}` on connect, then `ack` (`lane`, `replaced_pending`,
echoes `id`) or `error` per message, `error` when a send fails, and every `stats_interval`
s while active `{"type":"stats","lanes":{lane:{received,sent,dropped,errors,fps,last_seconds,busy,pending}}}`.
Query parameters override the server config: `/ws?scratch_slot=19&rhythm_interval=0.2`.

Rate limits are **not measured on hardware yet (TBD)**: `rhythm_interval` 0.12 s (the app's
rate), `play_interval` / `image_interval` / `band_interval` 0. A full-panel image takes ~4-8 s
to upload, so the image lane is well under 1 fps. Try it: `python examples/stream_demo.py --fake`
(no BLE) or `--url ws://jetson:8765/ws`.

## CLI

It calls the library directly; no server is needed (stop the server first). Global flags go
before the subcommand.

```
python3 -m maskapi scan
python3 -m maskapi diy face.png --slot 1 [--fit cover] [--avoid-eyes --margin 1 --valign top] [--no-play] [--preview]
python3 -m maskapi animation blink.gif [--start-slot 1] [--avoid-eyes]   # or frame files / a frame dir
python3 -m maskapi play 1 2 3 | delete 3 | count | loop                # play takes 1-20 slots
python3 -m maskapi shows [--order logo spiral session virgil] [--remove NAME]
python3 -m maskapi define-show tendrils images/tendrils --start-slot 11 [--force] [--avoid-eyes] [--no-play]
python3 -m maskapi play-show spiral | next | gesture shows
python3 -m maskapi place in.png out.png [--margin 1] [--valign center] [--no-avoid-eyes] [--preview]
python3 -m maskapi panel [--margin 1]                                  # print the oval / eye map
python3 -m maskapi text "HELLO" --color 00ff00 [--font-size 8] [--rainbow] [--preview]
python3 -m maskapi image icon.png --threshold 70 [--preview]
python3 -m maskapi brightness 40 | mode 3 | speed 5 | builtin-image 1 | builtin-anim 2
python3 -m maskapi gesture on --select diy --ids 1 2 12 11 | gesture off
python3 -m maskapi raw IMAG 1 0 --wait 1 | rawhex 064c494748543c
python3 -m maskapi --fake --shows /tmp/s.json define-show demo a.png    # global flags first
```

## HTTP endpoints

Interactive docs at `/docs`, schema at `/openapi.json`. Errors are `{"detail": ...}`: 400 bad
value or image, 404 unknown show, 409 slot overlap with another show, 422 validation, 503
link failed after retries, 502 the mask replied `ERROR`. Upload options can also be given as
query parameters (`?avoid_eyes=true&margin=1&valign=top`); the body wins.

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/status` | | connected, current show, last state set, last 10 notifications / commands, last_error |
| POST | `/connect`, `/disconnect` | | |
| POST | `/diy` | multipart `file` + `slot,fit,play,dry_run,avoid_eyes,margin,valign`, or JSON `image_base64,...` | image -> 46x58, PLAYs it by default; returns a PNG preview |
| POST | `/animation` | multipart `file` (one GIF or several images), or JSON `frames_base64` + `start_slot,fit,play,max_frames,avoid_eyes,...` | up to 20 frames; a longer GIF is subsampled to `max_frames` |
| POST | `/diy/play` | `{"slots":[1,2]}` | 1-20 slots |
| POST | `/diy/{slot}/play` | | |
| DELETE | `/diy` | `{"slots":[3]}` | |
| GET | `/diy/count` | | `CHEC` -> `{"count": n}` |
| GET | `/shows` | | `{path, order, current, shows: {name: {slots}}}` |
| PUT | `/shows/order` | `{"order":["logo","spiral"]}` | others keep their relative order after these |
| POST | `/shows/{name}` | like `/animation` + `start_slot` (default: the show's slots, else the first free run), `force`, `dry_run` | upload + record; `dropped` lists shows that were overwritten |
| POST | `/shows/{name}/play` | | |
| POST | `/next` | | play the next show in order |
| POST | `/gesture/our-shows` | optional `{"loop":"order|random"}` | `FACE` over the first slot of each show (stills) |
| GET | `/panel` | `?margin=1` | oval, eye cells, `rows` map (`#` usable, `E` eye, `m` margin, `.` outside) |
| WS | `/ws` | see [Streaming](#streaming-ws-maskapistreamstreamer) | |
| POST | `/text` | `{"text","color","rainbow","mode","speed","brightness","font_size","dry_run"}` | 16-row band |
| POST | `/image` | multipart or JSON, `width,threshold,brightness,mode,dry_run` | 16-row, one colour per column |
| POST | `/builtin/image/{idx}` | optional `{"extra":[..]}` | `IMAG idx extra..`; `/builtin/image/1` + `{"extra":[0]}` is the form that worked |
| POST | `/builtin/animation/{idx}` | optional `{"extra":[..]}` | `ANIM idx` |
| POST | `/builtin/loop` | | `LOOA` |
| POST | `/gesture` | `{"enable","loop":"order|random","select":"all|builtin|diy","ids":[]}` | `FACE`, up to 7 ids |
| POST | `/brightness` | `{"level":40}` | `LIGHT`, 1-255 |
| POST | `/mode`, `/speed` | `{"mode":3}`, `{"speed":5}` | |
| POST | `/color/foreground`, `/color/background` | `{"color":"ff0000","enable":true}` | `FC` / `BC` |
| POST | `/text-mode` | `{"enable":true,"mode":1}` | `M` |
| POST | `/rhythm` | `{"mode":0-4,"bars":[0-9 x <=24]}` | one frame to `...960b`; use `/ws` to stream |
| POST | `/raw` | `{"name":"LIGHT","args":[60]}` or `{"hex":"..."}`, plus `char:"cmd|music"`, `expect`, `wait` | returns the plaintext sent and notifications received |
| POST | `/upload` | `{"payload_hex"|"payload_base64","kind":"text|diy","bitmap_len","flag","slot","dats_hex","datcp_hex","after"}` | generic upload |

### curl

```
H=http://127.0.0.1:8765
curl -F file=@face.png -F slot=1 "$H/diy?avoid_eyes=true"
curl -F file=@face.png -F dry_run=true "$H/diy?avoid_eyes=true&valign=top"   # preview only
curl -F file=@f1.png -F file=@f2.png -F start_slot=11 -F force=true $H/shows/blink
curl -F file=@anim.gif $H/shows/spiral                    # reuses spiral's slots
curl $H/shows; curl -X POST $H/next; curl -X POST $H/shows/logo/play
curl -X POST $H/gesture/our-shows
curl "$H/panel?margin=1"
curl -H 'content-type: application/json' -d '{"slots":[2,3,4,5,6,7,8,9,10,11,12]}' $H/diy/play
curl -H 'content-type: application/json' -d '{"text":"HELLO","font_size":8}' $H/text
curl -H 'content-type: application/json' -d '{"extra":[0]}' $H/builtin/image/1
```

## What is verified

On the real mask, 2026-10-30 (Jetson host, webcam):

| Feature | Status |
|---|---|
| AES framing, notifications, 16-row text upload, `MODE`, `LIGHT`; text via the maskapi CLI incl. `--font-size 8` | verified |
| DIY 46x58 RGB888 column-major: `09 DATS [u16 BE len] 00 [slot] 01`, chunks `[n+1][idx][<=98]` each `REOK`, `09 DATCP [u32 BE time]` -> `DATCPOK` | verified, ~4-8 s per frame |
| Link drops mid-upload; per-frame retry (4 tries), drain + 1.5 s + reconnect-if-dropped | verified recipe (`diy.py`); maskapi implements the same, its retry path is **not yet run on the mask** |
| `PLAY` 1-10 (`[n+5] PLAY n slots`) and 11-20 (`19 PLAY n` + 10 slots, 0.1 s later `[n-10]` + rest) -> `PLAYOK` | verified (`diy.py`); maskapi's long form untested on the mask |
| `DELE` -> `DELEOK` | verified |
| Slot playback ~1.0 s/frame, no crossfade; `SPEED 1` ~3.5 s/frame, 10-255 ~1.0 s/frame | verified |
| `FACE` on: `0c FACE 01 00 02 04 01 02 0c 0b` -> `FACEOK`; off: `08 FACE 00 00 02 00`, no reply | verified. Gesture shows each slot as a still; the mask sends no notification on a wave |
| `IMAG`: `06 IMAG 01 00` lit a built-in full-face image; `05 IMAG idx` showed no change | partly; being re-tested |
| Panel is an oval; eye holes measured (`eyes.json`: left x5-17 y14-22, right x28-40 y14-20) | measured from a webcam photo |
| `CHEC` count, `ANIM`/`LOOA`, `FC`/`BC`/`M`, rhythm on `...960b` | from the decompiled app; untested |
| `/ws` streaming rates (PLAY switch latency, band fps, rhythm) | **TBD**, limits are configurable |
| Shows, placement, `/ws`, HTTP/CLI | tested against `FakeTransport` only (`python -m pytest tests`) |
