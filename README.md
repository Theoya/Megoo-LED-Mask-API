# LED-Mask

Custom images and animations for a **Megoo LED face mask**, driven from our own code instead
of the phone app. The mask is a "Shining Mask" clone: a 46 x 58 full-colour LED panel behind
a smoked visor, with eye holes, a hand-wave sensor and a Bluetooth LE controller.

Everything here was worked out from scratch in one session. The rest of this file covers what
we did, what we learned, and how to use it.

---

## Capabilities

The statuses below were established on the real mask on 2026-10-30, with the Jetson as the
BLE host and the webcam (`snap.py`, `frame_timer.py`) showing what the mask actually did.

### Confirmed on the real mask

| Capability | How | Notes |
|---|---|---|
| Full-panel images | `diy.py show`, `python3 -m maskapi diy`, `POST /diy` | 46 x 58, per-pixel RGB. The panel is an oval: the grid corners have no LEDs. |
| 20 on-mask DIY slots | upload, `PLAY`, `DELE` | Uploads take ~4-5 s per frame. `diy.py` retries/reconnects per frame. |
| Slot animations | `PLAY` of up to 20 slots | The >10 form (length byte 19 + continuation packet `[n-10][slots]`) works; the mask answered `PLAYOK`. The mask cycles about **1 frame/second** and cannot crossfade. |
| Playback speed | `SPEED` | Only slows slot playback: `SPEED 1` is ~3.5 s/frame, 10-255 are all ~1.0 s/frame. So ~1 fps is the ceiling; smoothness has to come from small steps and motion blur baked into the frames (the `spiral20` set, see [Animations](#animations)). |
| Built-in full-face image | `IMAG` | One test worked with plaintext `06 'IMAG' 01 00`. The app source says `05 'IMAG' idx`; both noted until it's pinned down. |
| Scrolling text (16-row band) | `python3 -m maskapi text "HELLO CLAUDE" --color ff4600` | Also the first real-device run of `maskapi`: connect, upload and scroll all worked. Options `--rainbow`, `--mode 1/3/4`, `--speed`, `--preview`. |
| 16-row text-channel images | `mask_upload.py` | One colour per column. |
| Brightness | `LIGHT` | |
| Generators | see [Files](#files) | Starburst logo (`starburst.py`, `make_icon.py`); full-panel logo `images/logo_full.png`; red oval spiral (`spiral.py`; `images/spiral`, smoothed `images/spiral20`); creepy tendril logo (`tendrils.py`; rendered, **not yet played** on the mask); scrolling terminal animation of a Claude Code session (`claude_session.py`, being added). |
| Webcam verification | `snap.py`, `frame_timer.py` | Photographs the mask; measures the playback interval. |

### Decoded from the official app, not yet tested on the mask

- **Gesture / hand-wave sensor** (`FACE`): cycle built-in or DIY slots, in order or at random
  (`maskapi gesture`, `POST /gesture`).
- **Music/rhythm mode**: stream 24 EQ bars to char `...960b` every ~120 ms (`POST /rhythm`).
- Text foreground/background colours (`FC`/`BC`/`M`), `ANIM`/`LOOA` built-in animations,
  `CHEC` slot count, `TIME` sync.

### Limits

- ~1 fps maximum playback and ~4 s per uploaded frame, so no live video.
- 20 slots.
- BLE range from the Jetson is ~1-2 m.
- One connection at a time: the phone app must be disconnected, and only one of our
  processes can hold the link.
- The PC's onboard Bluetooth is too weak to reach the mask.

---

## How we got here

### 1. Finding the mask on Bluetooth

The obvious route was the Windows PC, and it failed. Scans from the desktop's onboard Intel
Bluetooth only ever saw a couple of iPhones, at about -100 dBm. The radio barely hears
anything, most likely because the antenna isn't attached to the back panel.

We moved to the **Jetson** (`yahboom`, Realtek BT 5.1 USB radio). It saw about 20 devices,
including a Versa 2 watch and a TV, but still no mask.

**Getting the ID from the app.** With the mask on, we checked the iPhone's Bluetooth list and
the Shining Mask app. The mask showed up there as **`MASK_B042F1`**. That told us two things:

- its name prefix (`MASK`), so we knew what to scan for;
- that it was alive and advertising. We had been missing it because **the phone app was
  connected to it**, and a BLE device stops advertising while something is connected.

Once we closed the app and turned Bluetooth off in iOS **Settings** (Control Center isn't
enough), a filtered scan from the Jetson found it right away:

```
5F:F6:A0:B0:42:F1  name='MASK-B042F1'  service 0000fff0-...  (about -85 dBm)
```

On/off scans along the way also ruled out two lookalikes, "Pro" (`ffe0`) and an unnamed
`1910` device.

### 2. The 16-row text channel (first images)

The open-source [mask-go](https://github.com/GoneUp/mask-go) project documents the Shining
Mask protocol: AES-128-ECB encrypted 16-byte commands plus a chunked upload. Its upload is the
**scrolling-text** path, 16 rows tall, with one colour per column. We wrote `mask_upload.py`
for it and got the first image, two coloured "eyes", and then the Claude starburst logo.

Iterating on the logo on the real mask taught us a few things:

- The LEDs are roughly **square**, so a stretched icon looks stretched.
- Soft colours wash out to pink. Use saturated ones (`ff4600` orange).
- Shrinking the logo turns it into a blob. Redrawing the rays as lines (`starburst.py`) keeps it readable.

The text channel only reaches a 16-row band across the middle of the face. The mask clearly
had far more LEDs than that.

### 3. Finding the full panel

Two approaches ran in parallel:

- **Probing the mask** (`probe.py` + webcam `snap.py`). We sent raw commands and photographed
  the result. `IMAG` with a different length byte lit the **whole face** with a built-in image.
  That proved the panel is full-face and addressable.
- **Decompiling the Android app.** `cn.com.heaton.shiningmask` v1.2.3 was downloaded from
  APKPure and decompiled with jadx. We only read the code; nothing was installed or run. That
  gave the exact **DIY image format**:
  - 46 x 58 pixels, RGB888, column-major;
  - `DATS` with an image flag and a **slot** number (1-20);
  - `DATCP` with a timestamp;
  - `PLAY` / `DELE` / `CHEC` to show, delete and count stored images;
  - plus the gesture (`FACE`) and microphone/rhythm commands.

`diy.py` implements this. The full-screen logo was uploaded to slot 1 and confirmed on camera.

### 4. Animations

The app has no real custom-animation upload. Its only multi-frame uploader is dead code for an
older model. Instead, the mask **cycles through several DIY slots** when you `PLAY` them
together. `tendrils.py` renders a seamless loop of the logo with each ray swaying, curling and
stretching independently. The frames go into consecutive slots and are played together.

The app's long `PLAY` form (length byte 19 plus a continuation packet) lets one `PLAY` cover
all 20 slots, and the mask accepted it. Timing playback with `frame_timer.py` showed the
mask's limit: about one frame per second, no crossfade, and `SPEED` can only make it slower.
A 10-frame spiral looked jerky at that rate, so the 20-frame `spiral20` set is rendered as 80
small sub-steps and blended 4:1, which bakes motion blur into each frame.

### 5. API

`maskapi/` wraps all of this as a Python library, a CLI and a FastAPI HTTP server that owns
the single BLE connection. See [docs/API.md](docs/API.md).

---

## Setup

### Hardware

- Megoo / Shining Mask LED mask, charged and switched on.
- A Bluetooth LE radio **near the mask**. We use the Jetson; the desktop's onboard BT is too
  weak. Even from the Jetson the link is marginal (-80 to -93 dBm), so keep the mask within a
  metre or two of it.

### Before every session

1. Close the Shining Mask app on the phone and turn Bluetooth off in iOS **Settings**.
   Otherwise the phone holds the connection and the mask stays invisible.
2. Power the mask on. A **blue bar** on the mask means something has connected to it.

### Install (Jetson)

```
pip install --user -r requirements.txt   # bleak, pycryptodome, pillow, fastapi, uvicorn
python3 ble_scan.py                       # should list MASK-B042F1
```

Files are copied to the Jetson with `T:\Lifting-app\.tmp\jetson_ssh.py --upload <local> <remote>`
and commands run with `jetson_ssh.py script.sh`.

### Everyday use

```
# full-panel image (46x58, any colours) into slot 1 and show it
python3 diy.py show images/logo_full.png --slot 1 [--brightness 40]
python3 -m maskapi diy images/logo_full.png --slot 1 [--fit cover] [--preview]

# scrolling text on the 16-row band
python3 -m maskapi text "HELLO CLAUDE" --color ff4600 [--rainbow] [--mode 3] [--speed 5]

# 16-row text-channel image (legacy path)
python3 mask_upload.py image.png --threshold 70

# HTTP API
python3 -m maskapi serve        # http://127.0.0.1:8765/docs
```

Only one process can be connected to the mask at a time. Stop the API server before running
the scripts, and the reverse. The full CLI and endpoint list is in [docs/API.md](docs/API.md).

## Animations

The mask plays a set of slots as a loop at ~1 frame/second. `diy.py anim` deletes the target
slots, uploads each frame (with retry/reconnect), then sends one `PLAY` for all of them: up
to 20, using the long form above 10. Budget ~4-5 s of upload per frame. The generators write
`frameNN.png` plus `preview.gif` and `sheet.png` into their output folder; the `frame*.png`
glob skips the previews.

**Spiral** (red, shaped to the oval, seamless loop):

```
python3 spiral.py images/spiral --frames 10 [--arms 3 --twist 10 --color ff0000]
python3 diy.py anim images/spiral/frame*.png --first-slot 1
```

**Smoothed 20-frame spiral** (motion blur baked in). Render 80 sub-frames, then blend each
group of 4 into one frame. There is no script for the blend; it is an inline numpy step,
`0.55 * mean + 0.45 * max` of each group of 4 sub-frames (the mean smears the arms along their
path, the max keeps them bright):

```
python3 spiral.py images/spiral_sub --frames 80
python3 -c "
import glob, os, numpy as np
from PIL import Image
subs = [np.asarray(Image.open(p), float) for p in sorted(glob.glob('images/spiral_sub/frame*.png'))]
os.makedirs('images/spiral20', exist_ok=True)
for i in range(0, len(subs), 4):
    g = np.stack(subs[i:i + 4])
    out = 0.55 * g.mean(0) + 0.45 * g.max(0)
    Image.fromarray(out.clip(0, 255).astype(np.uint8)).save('images/spiral20/frame%02d.png' % (i // 4 + 1))
"
python3 diy.py anim images/spiral20/frame*.png --first-slot 1     # 20-slot PLAY
```

**Tendrils** (logo rays sway, curl and stretch independently; rendered, not yet played on
the mask):

```
python3 tendrils.py images/claude_symbol_source.webp images/tendrils --frames 10 [--color ff4600 --hub 5]
python3 diy.py anim images/tendrils/frame*.png --first-slot 2
```

**Replaying stored slots** without re-uploading: `python3 -m maskapi play 1 2 3`. The
library/CLI/API `play` takes up to 10 slots; for 11-20 use `diy.py anim`, which sends the
long form. `python3 -m maskapi speed 1` slows playback to ~3.5 s/frame.

**Checking it**: `python3 snap.py out.jpg [--crop x0,y0,x1,y1] [--scale 0.6]` takes a webcam
photo of the mask, and `python3 frame_timer.py 10` watches for 10 s and prints the median
interval between frame changes. Both run on the PC with the webcam (OpenCV).

---

## Files

| File | What it does |
|---|---|
| `diy.py` | Full-panel 46x58 upload to slots: `show` / `anim` (PLAY up to 20 slots), `--brightness` |
| `spiral.py` | Generates the red oval spiral loop frames |
| `tendrils.py` | Generates the creepy tendril animation frames |
| `claude_session.py` | Scrolling terminal animation of a Claude Code session (being added) |
| `starburst.py` | Redraws the logo as rays for small/16-row displays |
| `make_icon.py` | Fits any transparent icon into the 16-row band |
| `mask_upload.py` | 16-row text-channel uploader, shared BLE helpers |
| `probe.py` | Raw DATS upload / command sender for reverse-engineering (`--extra-cmd "IMAG 5"`, `"RAW <hex>"`) |
| `snap.py` | Webcam photo of the mask, for checking results |
| `frame_timer.py` | Webcam measurement of the mask's frame interval |
| `ble_scan.py` | Lists nearby BLE devices |
| `requirements.txt` | Python dependencies |
| `maskapi/__main__.py` | CLI, `python3 -m maskapi ...` ([docs/API.md](docs/API.md)) |
| `maskapi/server.py` | FastAPI HTTP server that owns the BLE link (`serve`) |
| `maskapi/client.py` | `Mask` async library: one lock, reconnect and retry |
| `maskapi/protocol.py` | Packet layouts and AES, no I/O |
| `maskapi/image.py` | Panel/text encoding, text rendering, frame loading, previews |
| `maskapi/transport.py` | bleak BLE transport and scan |
| `maskapi/fake.py` | In-memory fake mask for the tests and `--fake` |
| `tests/` | pytest suite (`test_protocol`, `test_image`, `test_client`, `test_api`), runs against the fake mask |
| `docs/API.md` | Library, CLI and HTTP API reference |
| `docs/maskapi.service` | Example systemd user unit for the API on the Jetson |
| `images/` | Source logo, rendered images, animation frames (`spiral`, `spiral_sub`, `spiral20`, `tendrils`), calibration grid |

## Protocol reference

All commands are a 16-byte plaintext `[len][ASCII name][args]`, zero-padded and encrypted with
AES-128-ECB, key `32672f7974ad43451d9c6c894a0e8764`. They are written to `d44bc439-...-9600`,
and replies arrive encrypted on `...9601`. Upload data is **plaintext** on `...960a` in chunks
of `[len+1][index][<=98 bytes]`, each acked with `REOK`.

| Command | Bytes | Notes |
|---|---|---|
| DIY upload | `09 DATS [u16 len] 00 [slot] 01` -> chunks -> `09 DATCP [u32 time]` | 8004-byte payload: 46x58 RGB, column-major |
| Text upload | `09 DATS [u16 len] [u16 bitmap_len] 00` -> chunks -> `05 DATCP` | 16-row bitmap + one RGB per column |
| PLAY | `[n+5] PLAY n slot...`; for n > 10: `13 PLAY n [10 slots]`, then `[n-10] slot...` | several slots = slideshow/animation at ~1 frame/s |
| DELE | `[n+5] DELE n slot...` | delete DIY slots |
| CHEC | `04 CHEC` | reply `CHEC` + stored count |
| IMAG / ANIM | `05 IMAG idx` / `05 ANIM idx` | built-in images / animations (app source); `06 IMAG 01 00` lit the full face in testing |
| LIGHT | `06 LIGHT v` | brightness |
| SPEED | `06 SPEED v` | slot playback: 1 ~ 3.5 s/frame, 10-255 ~ 1.0 s/frame |
| MODE | `05 MODE m` | text: 1 steady, 2 blink, 3/4 scroll |
| FACE | `[8+n] FACE enable loop select count ids...` | hand-wave gesture cycling |
| Rhythm | `0F mode [12 bytes]` on `...960b` | mic/music bars, ~120 ms |

## Credits

- Protocol basics: [GoneUp/mask-go](https://github.com/GoneUp/mask-go) and
  [shawnrancatore/shining-mask](https://github.com/shawnrancatore/shining-mask).
- Full-panel DIY, gesture and rhythm formats: read from the official Shining Mask Android app.
