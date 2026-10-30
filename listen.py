"""Stay connected and print every notification the mask sends: python listen.py [seconds]"""
import asyncio
import sys
import time

from bleak import BleakClient

from mask_upload import KEY, find_mask
from Crypto.Cipher import AES


async def main(secs):
    hit = await find_mask()
    if not hit:
        sys.exit("Mask not found.")
    client = BleakClient(hit[0], timeout=30.0)
    for _ in range(3):
        try:
            await client.connect()
            break
        except Exception as e:
            print(f"connect failed: {e!r}", flush=True)
    else:
        sys.exit("Could not connect.")
    t0 = time.time()

    def handler(char):
        def cb(_, data):
            raw = bytes(data)
            dec = AES.new(KEY, AES.MODE_ECB).decrypt(raw) if len(raw) % 16 == 0 else b""
            print(f"{time.time() - t0:6.2f}s {char[-4:]} raw={raw.hex()} dec={dec!r}", flush=True)
        return cb

    for svc in client.services:
        for ch in svc.characteristics:
            if "notify" in ch.properties or "indicate" in ch.properties:
                await client.start_notify(ch.uuid, handler(ch.uuid))
                print(f"listening on {ch.uuid}", flush=True)
    print(f"LISTENING {secs}s - wave now", flush=True)
    await asyncio.sleep(secs)
    await client.disconnect()
    print("done", flush=True)


asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 40))
