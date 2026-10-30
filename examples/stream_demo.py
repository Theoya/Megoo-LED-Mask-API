"""Tiny client for the /ws streaming endpoint.

  python examples/stream_demo.py --fake                        # in-process server + fake mask, no BLE
  python examples/stream_demo.py --url ws://jetson:8765/ws     # real server (pip install websockets)

Sends a slot switch, ~3 s of rhythm bars, a few full-panel frames (a moving dot,
latest frame wins) and one 16-row band frame, printing every reply and the stats.
"""
import argparse
import asyncio
import base64
import io
import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw  # noqa: E402


def dot_frame(i: int) -> bytes:
    img = Image.new("RGB", (46, 58))
    a = i * 0.6
    x, y = 23 + 12 * math.cos(a), 36 + 10 * math.sin(a)
    ImageDraw.Draw(img).ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 70, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def band_frame(text="HI") -> str:
    from maskapi.image import render_text

    buf = io.BytesIO()
    render_text(text, "00ff00").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def messages():
    """(delay_before, message) pairs; bytes = binary full-panel frame."""
    yield 0, {"type": "slot", "slot": 1}
    for k in range(25):  # rhythm: faster than the 120 ms limit on purpose, latest wins
        bars = [int(4.5 + 4.5 * math.sin(k * 0.5 + b * 0.4)) for b in range(24)]
        yield 0.06, {"type": "rhythm", "mode": 1, "bars": bars}
    for i in range(4):  # 4 frames in quick succession: the middle ones get dropped
        yield 0.05, dot_frame(i)
    yield 0.1, {"type": "band", "png_b64": band_frame()}
    yield 0.1, {"type": "stats"}


def show(msg):
    if msg.get("type") == "stats":
        lanes = {n: f"sent {l['sent']} drop {l['dropped']} fps {l['fps']}" for n, l in msg["lanes"].items()}
        print("stats", json.dumps(lanes))
    else:
        print(msg)


def run_fake(wait: float):
    from fastapi.testclient import TestClient

    from maskapi import Mask
    from maskapi.fake import FakeTransport
    from maskapi.server import create_app
    from maskapi.stream import StreamConfig

    mask = Mask(FakeTransport(), gap=0.02, shows=os.path.join(tempfile.gettempdir(), "maskapi_demo_shows.json"))
    with TestClient(create_app(mask, stream_config=StreamConfig(stats_interval=0.5))) as c:
        with c.websocket_connect("/ws") as ws:
            show(ws.receive_json())
            for delay, m in messages():
                time.sleep(delay)
                ws.send_bytes(m) if isinstance(m, bytes) else ws.send_json(m)
                show(ws.receive_json())
            end = time.time() + wait
            while time.time() < end:  # stats keep arriving while lanes are busy
                msg = ws.receive_json()
                show(msg)
                if msg["type"] == "stats" and not any(l["busy"] or l["pending"] for l in msg["lanes"].values()):
                    break
    t = mask.transport
    print(f"fake mask: playing {t.playing}, {len([w for w in t.writes])} rhythm writes, slots {sorted(t.slots)}")


async def run_url(url: str, wait: float):
    import websockets

    async with websockets.connect(url, max_size=None) as ws:
        async def reader():
            async for raw in ws:
                show(json.loads(raw))

        task = asyncio.ensure_future(reader())
        for delay, m in messages():
            await asyncio.sleep(delay)
            await ws.send(m if isinstance(m, bytes) else json.dumps(m))
        await asyncio.sleep(wait)  # uploads take ~4-8 s each on the real mask
        task.cancel()


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--fake", action="store_true", help="in-process server with the fake mask (no BLE)")
    g.add_argument("--url", help="ws://host:8765/ws of a running `python -m maskapi serve`")
    ap.add_argument("--wait", type=float, default=20.0, help="seconds to keep reading replies at the end")
    a = ap.parse_args()
    if a.fake:
        run_fake(a.wait)
    else:
        asyncio.run(run_url(a.url, a.wait))


if __name__ == "__main__":
    main()
