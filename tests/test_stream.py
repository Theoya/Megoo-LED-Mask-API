"""Streamer lanes (latest wins, rate limits) and the /ws endpoint, with the fake mask."""
import asyncio
import base64
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from maskapi import Mask
from maskapi import protocol as P
from maskapi.fake import FakeTransport
from maskapi.server import create_app
from maskapi.stream import StreamConfig, Streamer


def png(color=(255, 0, 0), size=(46, 58)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def mask(tmp_path, **kw):
    return Mask(FakeTransport(**kw), gap=0, ack_timeout=0.5, retry_wait=0, play_gap=0, shows=tmp_path / "s.json")


def test_play_and_show_coalesce(tmp_path):
    async def go():
        m = mask(tmp_path)
        async with Streamer(m, stats_interval=0) as s:
            acks = [await s.handle({"type": "slot", "slot": n, "id": n}) for n in (1, 2, 3)]
            assert acks[0] == {"type": "ack", "id": 1, "for": "slot", "lane": "play", "replaced_pending": False}
            await s.drain()
            assert m.transport.playing == [3]  # latest wins
            lane = s.lanes["play"]
            assert lane.received == 3 and lane.sent + lane.dropped == 3 and lane.sent >= 1
            await s.handle({"type": "show", "name": "spiral"})
            await s.drain()
            assert m.transport.playing == list(range(2, 11)) and m.shows.current == "spiral"
            assert (await s.handle({"type": "show", "name": "nope"}))["type"] == "error"
            assert (await s.handle({"type": "slot", "slot": 21}))["type"] == "error"
            assert (await s.handle({"type": "bogus"}))["type"] == "error"
            assert (await s.handle({"type": "stats"}))["lanes"]["play"]["sent"] >= 2
    asyncio.run(go())


def test_rhythm_rate_limit(tmp_path):
    async def go():
        m = mask(tmp_path)
        async with Streamer(m, rhythm_interval=0.1, stats_interval=0) as s:
            t0 = time.monotonic()
            for i in range(30):
                await s.handle({"type": "rhythm", "mode": 1, "bars": [i % 10] * 24})
                await asyncio.sleep(0.01)
            await s.drain()
            elapsed = time.monotonic() - t0
            writes = [w for w in m.transport.writes if w[0] == P.MUSIC_CHAR]
            assert len(writes) <= elapsed / 0.1 + 2
            assert writes[-1][1] == P.rhythm(1, [9] * 24)  # the newest frame was sent last
            assert (await s.handle({"type": "rhythm", "mode": 9, "bars": []}))["type"] == "error"
    asyncio.run(go())


def test_image_latest_frame_wins(tmp_path):
    async def go():
        m = mask(tmp_path)
        events = []
        async with Streamer(m, scratch_slot=19, stats_interval=0.05, on_event=events.append) as s:
            await s.handle(png((10, 0, 0)))                   # binary frame
            await asyncio.sleep(0)                            # first upload starts
            for c in (20, 30, 40):
                await s.handle({"type": "image", "png_b64": base64.b64encode(png((c, 0, 0))).decode()})
            await s.drain()
            await asyncio.sleep(0.1)
            lane = s.lanes["image"]
            assert lane.sent == 2 and lane.dropped == 2  # 20 and 30 never sent
            assert m.transport.slots[19][:3] == bytes([40, 0, 0]) and m.transport.playing == [19]
            assert [e for e in events if e["type"] == "stats"]
            assert (await s.handle({"type": "image", "png_b64": "!!"}))["type"] == "error"
    asyncio.run(go())


def test_band_and_lane_errors(tmp_path):
    async def go():
        m = mask(tmp_path)
        events = []
        async with Streamer(m, stats_interval=0, on_event=events.append) as s:
            await s.handle({"type": "band", "png_b64": base64.b64encode(png((0, 255, 0), (44, 16))).decode()})
            await s.drain()
            assert m.transport.dats[1] == 88 and m.transport.names()[-1] == "MODE"
            await s.handle({"type": "band", "png_b64": base64.b64encode(b"not a png").decode()})
            await s.drain()
            assert s.lanes["band"].errors == 1 and events[-1]["type"] == "error"
    asyncio.run(go())


def test_config_validation():
    with pytest.raises(ValueError):
        StreamConfig(scratch_slot=0)
    with pytest.raises(ValueError):
        StreamConfig(rhythm_interval=-1)


def test_websocket(tmp_path):
    fake = FakeTransport()
    m = Mask(fake, gap=0, ack_timeout=0.5, retry_wait=0, play_gap=0, shows=tmp_path / "s.json")
    with TestClient(create_app(m, stream_config=StreamConfig(stats_interval=0.05))) as c:
        with c.websocket_connect("/ws?scratch_slot=18") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello" and hello["config"]["scratch_slot"] == 18
            ws.send_json({"type": "slot", "slot": 5})
            assert ws.receive_json()["type"] == "ack"
            ws.send_bytes(png((0, 0, 99)))
            assert ws.receive_json()["lane"] == "image"
            ws.send_text("{nope")
            assert ws.receive_json()["type"] == "error"
            for _ in range(50):
                msg = ws.receive_json()
                if msg["type"] == "stats" and msg["lanes"]["image"]["sent"] == 1:
                    break
            else:
                raise AssertionError("no stats with the image sent")
    assert fake.slots[18][:3] == bytes([0, 0, 99]) and fake.playing == [18]
