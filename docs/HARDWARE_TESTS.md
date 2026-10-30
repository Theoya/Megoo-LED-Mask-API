# Hardware test log

Results on the real mask (`MASK-B042F1`, board ID `TR2111R004-01`), driven from the Jetson over BLE
and checked with the webcam (`snap.py`, `frame_timer.py`). Dates are 2026-10-29/30.

## Streaming and switching (`stream_test.py`)

| Test | Result | Verdict |
|---|---|---|
| `PLAY` single slot, wait for `PLAYOK` (`play --n 20`) | round trip median **105 ms** (60-240) | Fast to acknowledge |
| `PLAY` fired every 200 ms, no waiting (`fire --n 100`) | all 100 acknowledged in 21.4 s, but the **display changed only every ~0.4-3 s**, irregularly | The panel redraws at most ~1-2x/s; slot switching is not a fast stream |
| 16-row band bitmap upload per frame (`band --n 12`) | upload median **412 ms** (315-797); camera saw changes every 0.3-0.5 s | **~2-2.5 fps live**, but only the 16-row band |
| Full-panel 46x58 frame upload (`diy.py`) | **4-8 s** per frame | ~0.15-0.25 fps; fine for stored loops, too slow for live video |
| Rhythm/music bars to `...960b` (`rhythm --seconds 15`, mode 1) | 123 packets sent (8.2/s), no errors, **no visible change** | Inconclusive: the mask probably needs to be put into music mode first (command not identified yet) |
| Slot animation playback (PLAY of N slots) | ~**1.0 s/frame** | Firmware ceiling; no crossfade |
| `SPEED` | 1 -> ~3.5 s/frame; 10, 50, 100, 255 -> ~1.0 s/frame; 128 -> playback seemed to stop | Only slows playback |

**What this means for live content**

- Arbitrary live pixels: use the 16-row band (~2 fps).
- Full-face content: pre-render up to 20 frames into slots, then switch shows or slots live
  (acknowledged in ~0.1 s, visible within ~1 s).
- Values: the rhythm channel accepts 8+ packets/s but needs its mode entered first.

## Commands

| Command | Result |
|---|---|
| DIY upload `DATS..00 slot 01` / chunks / `DATCP time` | Works, `DATSOK`/`REOK`/`DATCPOK` |
| `PLAY` 1-10 slots, and the 20-slot long form (len 19 + continuation) | Works, `PLAYOK` |
| `DELE` | Works, `DELEOK` |
| `IMAG` as `06 'IMAG' 01 00` | Showed a full-face built-in image; `05 'IMAG' n` form untested since |
| `FACE` (gesture) limited to DIY slots | Works, `FACEOK`; a wave shows each slot as a **still**; no notification is sent on a wave |
| `FACE` with select mode 3 (undocumented) | `FACEOK`, behaviour not observed |
| `LIGHT` | Works |
| Text band (`maskapi text`), incl. `--font-size 8` | Works |
| Rhythm on `...960b` | No visible effect without entering music mode |
| `ANIM`, `LOOA`, `CHEC`, `TIME`, `FC`/`BC`/`M` | Not yet tested |
| OTA service `fd00` | **Never touched**; scripts refuse to write to it |

## Device identity (`gatt_dump.py`, read-only)

- Services: `fd00` (OTA, not read), `ae00` (`ae01` write, `ae02` notify), `fff0` (mask protocol).
- `...9600` user description: `TR2111R004-01` (the firmware string the app shows).
- Manufacturer data key `0x5254` ("TR"). No Device Information Service.
- See [HARDWARE.md](HARDWARE.md) for the chip analysis.
