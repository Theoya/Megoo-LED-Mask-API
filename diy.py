"""Full-panel (46x58, per-pixel RGB) DIY images and slot animations.

Format from the official app (cn.com.heaton.shiningmask 1.2.3):
  DATS 09 'DATS' [u16 BE len] 00 [slot] 01 -> DATSOK, chunks -> REOK each,
  DATCP 09 'DATCP' [u32 BE unix time] -> DATCPOK, then PLAY [n+5] 'PLAY' n slots...
Pixels are column-major (x outer, y inner), RGB888, no header.

  python diy.py show image.png [--slot 1]
  python diy.py anim frame01.png frame02.png ... [--first-slot 1]
"""
import argparse
import asyncio
import sys
import time

from bleak import BleakClient
from Crypto.Cipher import AES
from PIL import Image

from mask_upload import CHUNK, CMD_CHAR, KEY, NOTIFY_CHAR, UPLOAD_CHAR, decode_notify, find_mask

W, H = 46, 58


def raw(*parts) -> bytes:
    """Build an encrypted command from ints/bytes/str parts; first part is the length byte."""
    b = b"".join(p.encode() if isinstance(p, str) else bytes([p]) if isinstance(p, int) else p for p in parts)
    return AES.new(KEY, AES.MODE_ECB).encrypt(b.ljust(16, b"\x00"))


def encode(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    if img.size != (W, H):
        img = img.resize((W, H), Image.LANCZOS)
    px = img.load()
    return bytes(c for x in range(W) for y in range(H) for c in px[x, y])


class Mask:
    def __init__(self):
        self.client = None
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    async def __aenter__(self):
        hit = await find_mask()
        if not hit:
            sys.exit("Mask not found.")
        for attempt in range(1, 4):
            self.client = BleakClient(hit[0], timeout=30.0)
            try:
                await self.client.connect()
                await self.client.start_notify(NOTIFY_CHAR, lambda _, d: self.queue.put_nowait(decode_notify(d)))
                return self
            except Exception as e:
                print(f"  connect attempt {attempt} failed: {e!r}")
                await asyncio.sleep(2)
        sys.exit("Could not connect.")

    async def __aexit__(self, *exc):
        await self.client.disconnect()

    async def send(self, pkt: bytes):
        await self.client.write_gatt_char(CMD_CHAR, pkt, response=False)

    async def expect(self, prefix: str, timeout=6.0):
        while True:
            msg = await asyncio.wait_for(self.queue.get(), timeout)
            if msg.startswith(prefix):
                return msg
            if msg.startswith("ERROR"):
                raise RuntimeError(f"mask said {msg!r} while waiting for {prefix}")

    async def upload(self, slot: int, img: Image.Image):
        data = encode(img)
        await self.send(raw(9, "DATS", len(data).to_bytes(2, "big"), 0, slot, 1))
        await self.expect("DATSOK")
        for idx, off in enumerate(range(0, len(data), CHUNK)):
            part = data[off : off + CHUNK]
            await self.client.write_gatt_char(UPLOAD_CHAR, bytes([len(part) + 1, idx & 0xFF]) + part, response=False)
            await self.expect("REOK")
        await self.send(raw(9, "DATCP", int(time.time()).to_bytes(4, "big")))
        await self.expect("DATCPOK")

    async def play(self, slots):
        slots = list(slots)[:10]
        await self.send(raw(len(slots) + 5, "PLAY", len(slots), bytes(slots)))
        await self.expect("PLAYOK")

    async def delete(self, slots):
        slots = list(slots)
        for i in range(0, len(slots), 10):
            grp = slots[i : i + 10]
            await self.send(raw(len(grp) + 5, "DELE", len(grp), bytes(grp)))
            await self.expect("DELEOK")

    async def brightness(self, v: int):
        await self.send(raw(6, "LIGHT", max(1, v)))


async def main_async(a):
    imgs = [Image.open(p) for p in a.images]
    first = a.slot if a.cmd == "show" else a.first_slot
    slots = list(range(first, first + len(imgs)))
    async with Mask() as m:
        try:
            await m.delete(slots)
        except Exception as e:  # empty slots may not ack
            print(f"  (delete: {e!r})")
        for s, img in zip(slots, imgs):
            t = time.time()
            await m.upload(s, img)
            print(f"slot {s} uploaded ({time.time() - t:.1f}s)")
        if a.brightness:
            await m.brightness(a.brightness)
        await m.play(slots)
        print(f"playing slots {slots}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["show", "anim"])
    ap.add_argument("images", nargs="+")
    ap.add_argument("--slot", type=int, default=1)
    ap.add_argument("--first-slot", type=int, default=1)
    ap.add_argument("--brightness", type=int, default=0)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
