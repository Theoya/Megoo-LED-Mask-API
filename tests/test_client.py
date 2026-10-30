"""Mask client against the in-memory FakeTransport: upload flows, retries, errors."""
import asyncio

import pytest
from PIL import Image

from maskapi import AckTimeout, Mask, MaskError
from maskapi import protocol as P
from maskapi.fake import FakeTransport


def run(coro):
    return asyncio.run(coro)


def mask(**kw):
    return Mask(FakeTransport(**kw), gap=0, ack_timeout=0.5, retry_wait=0, play_gap=0)


def test_text_upload_flow():
    async def go():
        m = mask()
        f = await m.show_text("HI", "ff0000", brightness=50)
        t = m.transport
        assert t.names()[0] == "DATS" and t.names()[1] == "DATCP"
        assert t.names()[2:] == ["MODE", "LIGHT"]
        assert t.dats == (len(f.payload), len(f.bitmap), 0)
        assert t.completed == [f.payload]
        assert P.describe(t.commands[2]) == "MODE 01"
        assert m.state["text"] == "HI" and m.state["brightness"] == 50
        long = await m.show_text("A MUCH LONGER MESSAGE")
        assert m.state["mode"] == P.MODE_SCROLL_LEFT and long.width > 44
    run(go())


def test_diy_upload_flow():
    async def go():
        m = mask()
        panel = await m.upload_diy(Image.new("RGB", (46, 58), (0, 0, 255)), slot=4)
        t = m.transport
        assert t.dats == (8004, 4, 1)
        assert t.slots[4] == panel.payload and len(t.uploads) == 82
        datcp = [c for c in t.commands if c[1:6] == b"DATCP"][0]
        assert datcp[0] == 9 and abs(int.from_bytes(datcp[6:10], "big") - __import__("time").time()) < 60
        assert t.playing == [4] and m.state["slots"] == [4]
        assert await m.diy_count() == 1
        await m.delete_slots([4])
        assert t.slots == {} and await m.diy_count() == 0
    run(go())


def test_animation_and_play():
    async def go():
        m = mask()
        frames = [Image.new("RGB", (10, 10), (i * 40, 0, 0)) for i in range(3)]
        slots = await m.upload_animation(frames, start_slot=5)
        assert slots == [5, 6, 7] and sorted(m.transport.slots) == [5, 6, 7]
        assert m.transport.playing == [5, 6, 7]
        with pytest.raises(ValueError):
            await m.upload_animation(frames * 7)  # 21 > 20
        with pytest.raises(ValueError):
            await m.upload_animation(frames, start_slot=19)
    run(go())


def test_simple_commands():
    async def go():
        m = mask()
        await m.set_brightness(30)
        await m.show_builtin_image(0)
        await m.show_builtin_animation(2)
        await m.loop_builtin_animations()
        await m.play_diy_slot(3)
        await m.set_foreground("00ff00")
        await m.set_gesture(True, True, P.SELECT_DIY, [1, 2])
        await m.send_rhythm(1, [9] * 24)
        d = [P.describe(c) for c in m.transport.commands]
        assert d == ["LIGHT 1e", "IMAG 00", "ANIM 02", "LOOA", "PLAY 01 03", "FC 01 00 ff 00",
                     "FACE 01 01 02 02 01 02"]
        char, plain = m.transport.writes[0]
        assert char == P.MUSIC_CHAR and plain[:3] == bytes([0x0F, 1, 0x99])
    run(go())


def test_raw_and_collect():
    async def go():
        m = Mask(FakeTransport(replies={"PING": "PONG"}), gap=0)
        assert await m.send_command("PING", wait=0.2) == ["PONG"]
        assert await m.send_command("PING", expect="PONG") == ["PONG"]
        await m.send_raw(bytes.fromhex("064c49474854ff"))
        assert m.transport.commands[-1] == P.plaintext("LIGHT", 255)
    run(go())


def test_retry_on_connect_and_gatt_failures():
    async def go():
        m = mask(fail_connects=1, fail_writes=1)
        await m.upload_diy(Image.new("RGB", (1, 1)), slot=1)
        assert m.transport.connects == 3  # 1 failed, 1 ok then GATT error mid-upload, 1 reconnect
        assert m.transport.slots[1] == bytes(8004)
        assert m.last_error is None
    run(go())


def test_gives_up_after_retries():
    async def go():
        m = mask(fail_connects=10)
        with pytest.raises(Exception):
            await m.set_brightness(10)
        assert m.transport.connects == 4 and "fake connect failure" in m.last_error
    run(go())


def test_ack_timeout_and_error():
    async def go():
        m = mask(ack=False)
        m.retries = 2
        with pytest.raises(AckTimeout):
            await m.play_slots([1])
        # still connected, so no reconnect; both attempts re-sent PLAY
        assert m.transport.connects == 1 and m.transport.names().count("PLAY") == 2
        m = mask(error_on="DATCP")
        with pytest.raises(MaskError):
            await m.upload_diy(Image.new("RGB", (1, 1)), 2)
        assert m.transport.connects == 1  # ERROR is not retried
    run(go())


def test_lock_serialises_operations():
    async def go():
        m = Mask(FakeTransport(), gap=0.001)
        await asyncio.gather(m.show_text("A"), m.upload_diy(Image.new("RGB", (1, 1)), 3), m.show_text("B"))
        names = m.transport.names()
        # each upload's DATS is followed by its own DATCP before the next DATS
        seq = [n for n in names if n in ("DATS", "DATCP")]
        assert seq == ["DATS", "DATCP"] * 3
    run(go())


def test_idle_disconnect():
    async def go():
        m = Mask(FakeTransport(), gap=0, idle_disconnect=0.05)
        await m.set_brightness(5)
        assert m.connected
        await asyncio.sleep(0.15)
        assert not m.connected
        await m.set_brightness(6)
        assert m.connected and m.transport.connects == 2
    run(go())
