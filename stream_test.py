"""Measure what can be streamed to the mask, using only commands the official app sends.

Safety: never touches the OTA service (fd00/fd01/fd02); stops on any ERROR reply;
only rewrites the scratch slot given by --scratch-slot (default: none).

  python stream_test.py play  --slots 1 11 --n 20        # PLAY switch latency
  python stream_test.py fire  --slots 1 11 --interval 0.25 --n 20   # PLAY without waiting for acks
  python stream_test.py band  --n 8                       # 16-row text-channel bitmap uploads
  python stream_test.py rhythm --seconds 10 --interval 0.12 [--mode 1]
"""
import argparse
import asyncio
import math
import statistics
import sys
import time

from bleak import BleakClient
from Crypto.Cipher import AES

from mask_upload import CHUNK, CMD_CHAR, KEY, NOTIFY_CHAR, UPLOAD_CHAR, decode_notify, find_mask

RHYTHM_CHAR = "d44bc439-abfd-45a2-b575-92541612960b"
FORBIDDEN = ("0000fd01", "0000fd02", "0000ae01")  # OTA (Panchip fd00, likely JieLi ae00): never write


def enc(b: bytes) -> bytes:
    return AES.new(KEY, AES.MODE_ECB).encrypt(b.ljust(16, b"\x00"))


def raw(*parts) -> bytes:
    return enc(b"".join(p.encode() if isinstance(p, str) else bytes([p]) if isinstance(p, int) else p for p in parts))


def play_pkt(slots):
    return raw(len(slots) + 5, "PLAY", len(slots), bytes(slots))


class Link:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.log = []

    async def open(self):
        hit = await find_mask()
        if not hit:
            sys.exit("Mask not found.")
        for attempt in range(3):
            self.c = BleakClient(hit[0], timeout=30.0)
            try:
                await self.c.connect()
                break
            except Exception as e:
                print(f"connect failed: {e!r}", flush=True)
        else:
            sys.exit("Could not connect.")

        def cb(_, d):
            msg = decode_notify(d)
            self.log.append((time.perf_counter(), msg))
            if msg.startswith("ERROR"):
                print("mask replied ERROR - stopping", flush=True)
            self.q.put_nowait(msg)

        await self.c.start_notify(NOTIFY_CHAR, cb)

    async def write(self, char, data):
        assert not any(f in char for f in FORBIDDEN)
        await self.c.write_gatt_char(char, data, response=False)

    async def expect(self, prefix, timeout=5.0):
        while True:
            msg = await asyncio.wait_for(self.q.get(), timeout)
            if msg.startswith("ERROR"):
                raise SystemExit("ERROR from mask")
            if msg.startswith(prefix):
                return msg


def summary(name, xs):
    xs = [x * 1000 for x in xs]
    print(f"{name}: n={len(xs)} median={statistics.median(xs):.0f}ms min={min(xs):.0f} max={max(xs):.0f}", flush=True)


async def t_play(L, a):
    lat = []
    for i in range(a.n):
        s = a.slots[i % len(a.slots)]
        t = time.perf_counter()
        await L.write(CMD_CHAR, play_pkt([s]))
        await L.expect("PLAYOK")
        lat.append(time.perf_counter() - t)
    summary("PLAY round trip", lat)


async def t_fire(L, a):
    t0 = time.perf_counter()
    for i in range(a.n):
        await L.write(CMD_CHAR, play_pkt([a.slots[i % len(a.slots)]]))
        await asyncio.sleep(a.interval)
    await asyncio.sleep(1)
    oks = sum(1 for _, m in L.log if m.startswith("PLAYOK"))
    print(f"fired {a.n} PLAYs at {a.interval}s in {time.perf_counter() - t0:.1f}s, PLAYOK replies {oks}", flush=True)


def band_payload(k, width=44):
    """Moving vertical bar on the 16-row channel, one colour per column."""
    bitmap, colors = bytearray(), bytearray()
    for x in range(width):
        on = abs(x - (k * 5) % width) < 3
        bitmap += (0xFFFF if on else 0).to_bytes(2, "little")
        colors += bytes((255, 40, 0)) if on else b"\x00\x00\x00"
    return bytes(bitmap), bytes(colors)


async def t_band(L, a):
    times = []
    for k in range(a.n):
        bm, col = band_payload(k)
        data = bm + col
        t = time.perf_counter()
        await L.write(CMD_CHAR, raw(9, "DATS", len(data).to_bytes(2, "big"), len(bm).to_bytes(2, "big"), 0))
        await L.expect("DATSOK")
        for idx, off in enumerate(range(0, len(data), CHUNK)):
            part = data[off : off + CHUNK]
            await L.write(UPLOAD_CHAR, bytes([len(part) + 1, idx & 0xFF]) + part)
            await L.expect("REOK")
        await L.write(CMD_CHAR, raw(5, "DATCP"))
        await L.expect("DATCPOK")
        if k == 0:
            await L.write(CMD_CHAR, raw(5, "MODE", 1))
        times.append(time.perf_counter() - t)
    summary("band frame upload", times)


async def t_rhythm(L, a):
    """App format: [0x0F][mode][12 bytes = 24 bars 0..9, two per byte, high nibble first], encrypted."""
    t0, n = time.perf_counter(), 0
    while time.perf_counter() - t0 < a.seconds:
        ph = (time.perf_counter() - t0) * 3
        bars = [max(0, min(9, round(4.5 + 4.5 * math.sin(ph + i * 0.5)))) for i in range(24)]
        packed = bytes((bars[i] << 4) | bars[i + 1] for i in range(0, 24, 2))
        await L.write(RHYTHM_CHAR, enc(bytes([0x0F, a.mode]) + packed))
        n += 1
        await asyncio.sleep(a.interval)
    print(f"rhythm: sent {n} frames in {a.seconds}s ({n / a.seconds:.1f}/s), notifies {[m for _, m in L.log][-5:]}", flush=True)


async def main(a):
    L = Link()
    await L.open()
    try:
        await {"play": t_play, "fire": t_fire, "band": t_band, "rhythm": t_rhythm}[a.test](L, a)
        if a.restore:
            await L.write(CMD_CHAR, play_pkt(a.restore))
    finally:
        await L.c.disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("test", choices=["play", "fire", "band", "rhythm"])
    ap.add_argument("--slots", type=int, nargs="+", default=[1, 11])
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--mode", type=int, default=1)
    ap.add_argument("--restore", type=int, nargs="*", default=None, help="slots to PLAY at the end")
    asyncio.run(main(ap.parse_args()))
