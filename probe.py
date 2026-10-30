"""Send a raw DATS upload with arbitrary parameters, for reverse-engineering the panel.

  python probe.py payload.bin --bitmap-len N [--flag 0] [--mode 1] [--extra-cmd "PLAY 1 1"]

payload.bin is the full byte stream (bitmap followed by colours). DATS gets
total=len(payload), bitmap=--bitmap-len, and --flag as the last byte.
"""
import argparse
import asyncio
import sys

from bleak import BleakClient
from Crypto.Cipher import AES

from mask_upload import CHUNK, CMD_CHAR, KEY, NOTIFY_CHAR, UPLOAD_CHAR, cmd, decode_notify, find_mask


async def run(a, payload: bytes):
    hit = await find_mask()
    if not hit:
        sys.exit("Mask not found.")
    client = BleakClient(hit[0], timeout=30.0)
    for attempt in range(3):
        try:
            await client.connect()
            break
        except Exception as e:
            print(f"  connect failed: {e!r}")
    else:
        sys.exit("Could not connect.")

    log = []

    def on_notify(_, d):
        msg = decode_notify(d)
        log.append(msg)
        queue.put_nowait(msg)

    queue: asyncio.Queue[str] = asyncio.Queue()

    async def expect(prefix, timeout=5.0):
        while True:
            msg = await asyncio.wait_for(queue.get(), timeout)
            if msg.startswith(prefix):
                return
            print(f"  notify {msg!r}")

    try:
        await client.start_notify(NOTIFY_CHAR, on_notify)
        send = lambda pkt: client.write_gatt_char(CMD_CHAR, pkt, response=False)
        if payload:
            await send(cmd("DATS", *len(payload).to_bytes(2, "big"), *a.bitmap_len.to_bytes(2, "big"), a.flag))
            await expect("DATSOK")
            for idx, off in enumerate(range(0, len(payload), CHUNK)):
                part = payload[off : off + CHUNK]
                await client.write_gatt_char(UPLOAD_CHAR, bytes([len(part) + 1, idx & 0xFF]) + part, response=False)
                await expect("REOK")
            await send(cmd("DATCP"))
            await expect("DATCPOK")
        if a.mode is not None:
            await send(cmd("MODE", a.mode))
        for extra in a.extra_cmd or []:
            name, *args = extra.split()
            if name == "RAW":  # RAW <hex plaintext>, padded to 16 and encrypted as-is
                await send(AES.new(KEY, AES.MODE_ECB).encrypt(bytes.fromhex("".join(args)).ljust(16, b"\x00")))
            else:
                await send(cmd(name, *(int(x, 0) for x in args)))
            await asyncio.sleep(0.4)
        await asyncio.sleep(1.5)
    except asyncio.TimeoutError:
        print("  TIMEOUT waiting for ack")
    finally:
        await client.disconnect()
    print(f"notifies: {log}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payload", nargs="?")
    ap.add_argument("--bitmap-len", type=int, default=0)
    ap.add_argument("--flag", type=int, default=0)
    ap.add_argument("--mode", type=int, default=None)
    ap.add_argument("--extra-cmd", action="append", help='e.g. "IMAG 5" or "PLAY 1 1"')
    a = ap.parse_args()
    payload = open(a.payload, "rb").read() if a.payload else b""
    asyncio.run(run(a, payload))


if __name__ == "__main__":
    main()
