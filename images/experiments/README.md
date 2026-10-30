# Experiments (preview only, not yet played on the mask)

Original procedural animations for the 46x58 panel. Each folder holds `frameNN.png` (exactly
46x58 RGB, cells without an LED written black), `preview.gif` (6x, 1000 ms per frame like the
real ~1 fps slot playback, no crossfade) and `sheet.png` (all frames at 4x; on the sheet only,
cells with no LED (outside the oval, eye holes) are tinted dark grey so the layout is visible).
All loops are seamless (last frame flows into frame 1). Run the commands from the repo root.
Shared helpers: `experiments/_common.py`.

| Name | What it is | Regenerate | Frames |
|---|---|---|---|
| `embers` | Low fire curling along the chin, sparks breaking off and rising past the eyes; eye holes flicker with an orange underglow | `python3 experiments/embers.py` | 20 |
| `glyphrain` | Columns of original runic 3x4 glyphs lit by cyan drops falling ~1 glyph per frame; the rain parts around the eye holes, whose rims glow as drops pass | `python3 experiments/glyphrain.py [--color 00f0ff --trail 0030ff]` | 20 |
| `heartbeat` | Dark-red knot beats lub-dub, the pulse runs out along a vein network that wraps around the eyes, blood seeps back, the knot clenches | `python3 experiments/heartbeat.py [--seed 12]` | 12 |
| `watchers` | The eye holes glow from inside: a hot pupil glances left, right, flares, smoulders; smoke curls off the brow | `python3 experiments/watchers.py [--palette acid\|ember\|violet]` | 16 |
| `lavalamp` | Metaballs rising, sinking, fusing; violet glow, magenta skin, orange where blobs merge | `python3 experiments/lavalamp.py [--scheme magma\|abyss\|toxic]` | 20 |
| `creature` | Original six-eyed grinner: amber-rimmed eye holes, four small extra eyes open one by one, a fanged grin widens and opens on a red throat, full blink | `python3 experiments/creature.py` | 14 |

Most generators also take `--frames N` (heartbeat and watchers rescale their timing; fewer
frames = bigger steps per 1 s cut) and `--out DIR`.

## Uploading one later

The 20 slots are currently fully used: **logo (1) / spiral (2-10) / image (11) / session
(12-20)**. Trying an experiment means temporarily overwriting a show and re-uploading it
afterwards. Pass the `frame*.png` glob (not the folder) so the preview files are skipped, and
do **not** pass `--avoid-eyes`. Exact 46x58 frames then go up unchanged; with `--avoid-eyes` the
image would be cropped and rescaled.

20-frame sets take all 20 slots (everything else gets overwritten):

```
python3 diy.py anim images/experiments/embers/frame*.png --first-slot 1
python3 -m maskapi define-show embers images/experiments/embers/frame*.png --start-slot 1 --force
# same for glyphrain, lavalamp
```

Shorter sets, placed to overwrite as little as possible:

```
# heartbeat (12): slots 9-20, overwrites the end of spiral, image and session
python3 diy.py anim images/experiments/heartbeat/frame*.png --first-slot 9
python3 -m maskapi define-show heartbeat images/experiments/heartbeat/frame*.png --start-slot 9 --force

# creature (14): slots 7-20
python3 diy.py anim images/experiments/creature/frame*.png --first-slot 7
python3 -m maskapi define-show creature images/experiments/creature/frame*.png --start-slot 7 --force

# watchers (16): slots 5-20
python3 diy.py anim images/experiments/watchers/frame*.png --first-slot 5
python3 -m maskapi define-show watchers images/experiments/watchers/frame*.png --start-slot 5 --force
```

To fit one into a single existing show without touching the others, render 9 frames and use the
session slots, e.g. `python3 experiments/heartbeat.py --frames 9 --out images/experiments/heartbeat9`
then `--first-slot 12` / `--start-slot 12`. `--force` drops every show whose slots were
overwritten from the registry. Afterwards, restore the originals by re-uploading them (see the
main README).

Budget ~4-5 s of upload per frame (a 20-frame set takes ~1.5 min).
