"""Shows registry and the Mask show/PLAY/retry methods against the fake mask."""
import asyncio
import json

import pytest
from PIL import Image

from maskapi import Mask
from maskapi import protocol as P
from maskapi.fake import FakeTransport
from maskapi.shows import ShowError, ShowRegistry


def frames(n):
    return [Image.new("RGB", (46, 58), (10 * i, 0, 0)) for i in range(n)]


def mask(tmp_path, transport=None, **kw):
    kw = {"gap": 0, "ack_timeout": 0.5, "retry_wait": 0, "play_gap": 0, **kw}
    return Mask(transport or FakeTransport(), shows=tmp_path / "s.json", **kw)


def test_default_layout_and_persistence(tmp_path):
    path = tmp_path / "shows.json"
    r = ShowRegistry(path)
    assert r.slots("logo") == [1] and r.slots("virgil") == [11]
    assert r.slots("spiral") == list(range(2, 11)) and r.slots("session") == list(range(12, 21))
    assert r.order == ["logo", "spiral", "session", "virgil"]
    assert not path.exists()  # nothing written until something changes
    assert r.next_name() == "logo"
    r.set_current("logo")
    assert ShowRegistry(path).current == "logo" and ShowRegistry(path).next_name() == "spiral"
    assert r.gesture_ids() == [1, 2, 12, 11]  # the FACE ids verified on the mask
    r.set_order(["virgil", "logo"])
    assert ShowRegistry(path).order == ["virgil", "logo", "spiral", "session"]


def test_overlap_guard(tmp_path):
    r = ShowRegistry(tmp_path / "s.json")
    with pytest.raises(ShowError, match="overlap"):
        r.plan("new", 3, 5)
    with pytest.raises(ShowError):
        r.plan("new", 1)  # all 20 slots are taken by the default layout
    assert r.plan("spiral", 5) == [2, 3, 4, 5, 6]  # redefining reuses its own start
    assert r.plan("new", 3, 5, force=True) == [5, 6, 7]
    assert r.define("new", [5, 6, 7], force=True) == ["spiral"]
    assert "spiral" not in r.order and r.free_run(3) == 2
    with pytest.raises(ShowError):
        r.plan("bad name!", 1, 2)
    with pytest.raises(ValueError):
        r.plan("x", 3, 19, force=True)
    r.remove("new")
    assert "new" not in json.loads((tmp_path / "s.json").read_text())["shows"]


def test_mask_shows_flow(tmp_path):
    async def go():
        m = mask(tmp_path)
        t = m.transport
        r = await m.define_show("spiral", frames(12), force=True)  # 2..13 overlaps virgil + session
        assert r["slots"] == list(range(2, 14)) and set(r["dropped"]) == {"virgil", "session"}
        assert t.playing == list(range(2, 14))  # long PLAY round-trip through the fake
        assert t.names()[0] == "DELE" and sorted(t.slots) == list(range(2, 14))
        assert m.shows.current == "spiral"
        assert await m.next_show() == "logo" and t.playing == [1]
        assert await m.next_show() == "spiral"
        assert await m.play_show("logo") == [1]
        assert await m.gesture_our_shows() == [1, 2]
        assert P.describe(t.commands[-1]) == "FACE 01 00 02 02 01 02"
        with pytest.raises(ShowError):
            await m.play_show("nope")
        assert ShowRegistry(tmp_path / "s.json").current == "logo"
        with pytest.raises(ShowError):
            await m.define_show("other", frames(2), start_slot=1)  # overlaps logo
    asyncio.run(go())


def test_define_show_from_dir_and_avoid_eyes(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    for i in range(3):
        Image.new("RGB", (46, 58), (255, 255, 255)).save(d / f"frame{i + 1:02d}.png")
    Image.new("RGB", (5, 5)).save(d / "sheet.png")  # not a frame*.png: ignored

    async def go():
        m = mask(tmp_path)
        r = await m.define_show("white", [str(d)], start_slot=18, avoid_eyes=True, force=True, play=False)
        assert r["slots"] == [18, 19, 20]
        data = m.transport.slots[18]
        off = (10 * 58 + 17) * 3  # pixel (10, 17), inside the image-left eye hole; column-major
        assert data[off : off + 3] == b"\x00\x00\x00"
        assert any(data)
        assert m.transport.playing == []
    asyncio.run(go())


class SilentDele(FakeTransport):
    def _command(self, plain):
        if plain[1:5] == b"DELE":
            self.commands.append(plain)
            return
        super()._command(plain)


def test_long_play_and_silent_dele(tmp_path):
    async def go():
        m = mask(tmp_path)
        await m.play_slots(list(range(1, 21)))
        assert m.transport.playing == list(range(1, 21))
        first, second = m.transport.commands[-2:]
        assert first[0] == 19 and first[5] == 20 and second[:2] == bytes([10, 11])
        m2 = mask(tmp_path, SilentDele(), ack_timeout=0.1)
        await m2.upload_diy(Image.new("RGB", (1, 1)), 4)  # DELE unanswered: one try, then upload
        assert 4 in m2.transport.slots and m2.transport.names().count("DELE") == 1
    asyncio.run(go())


def test_per_frame_retry_reconnects_only_when_dropped(tmp_path):
    async def go():
        t = FakeTransport(fail_writes=2)
        m = mask(tmp_path, t)
        await m.upload_animation(frames(3), start_slot=1, delete_first=False)
        assert sorted(t.slots) == [1, 2, 3] and t.playing == [1, 2, 3]
        assert t.connects == 3  # each fake GATT failure drops the link, so 2 reconnects
    asyncio.run(go())
