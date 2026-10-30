# LED-Mask

Custom images for a Megoo LED face mask (`MASK-B042F1`), which speaks the
"Shining Mask" BLE protocol (reverse-engineered by [GoneUp/mask-go](https://github.com/GoneUp/mask-go)).

## Display

- 44 x 16 LEDs, roughly square, each one on or off.
- **One colour per column.** Bold shapes and icons work; photos become silhouettes.
- Image left = the mask's right eye (correct for people looking at you).
- LEDs wash out soft colours. Use saturated ones, e.g. `ff4600` for orange.

## Usage

```
pip install -r requirements.txt

python make_icon.py images/claude_symbol_source.webp images/out.png --size 20
python mask_upload.py images/out.png --preview          # ASCII preview
python mask_upload.py images/out.png --threshold 70     # upload
```

`mask_upload.py` options: `--brightness` (default 60), `--threshold` (0-255; pixels
brighter than this light up), `--width` (44), `--scan` (just look for the mask).

## Bluetooth notes

- **Run uploads from the Jetson.** The desktop's onboard Bluetooth barely hears anything
  (about -100 dBm). Copy the script and image over with `T:\Lifting-app\.tmp\jetson_ssh.py --upload`,
  then run it there.
- The mask stops advertising while the iPhone Shining Mask app is connected. Close the app and turn
  Bluetooth off in iOS **Settings**, not Control Center.
- The signal is weak (about -80 dBm). The first connect often fails, so the script retries 3 times.
  A blue bar on the mask means it is connected.

## Protocol summary

- Service `fff0`. Commands go to `...29600`, notifications come from `...29601`, image data goes to `...2960a`.
- Commands are 16 bytes, `[len][ASCII cmd][args]`, AES-128-ECB with key `32672f7974ad43451d9c6c894a0e8764`.
- Upload: `DATS` -> wait `DATSOK` -> chunks of `[len][idx][<=98 bytes]`, each waiting for `REOK` -> `DATCP` -> wait `DATCPOK`.
  Then `MODE 1` (steady) and `LIGHT n`.
- Payload: per-column 16-bit bitmap (LE; byte 0 = rows 0-7 MSB-first), then per-column RGB.
