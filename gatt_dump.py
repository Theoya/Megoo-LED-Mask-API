"""Read-only GATT inventory of the mask: services, characteristics, readable values, descriptors.

Never writes anything, and never reads from the OTA service (fd00), to stay clear of it.

  python gatt_dump.py
"""
import asyncio
import sys

from bleak import BleakClient

from mask_upload import find_mask

SKIP_SERVICES = ("0000fd00",)


def show(v: bytes) -> str:
    txt = v.decode("utf-8", "replace") if v and all(32 <= b < 127 for b in v) else ""
    return f"{v.hex()}" + (f"  '{txt}'" if txt else "")


async def main():
    hit = await find_mask()
    if not hit:
        sys.exit("Mask not found.")
    dev, name, rssi = hit
    print(f"{name} {dev.address} {rssi} dBm")
    async with BleakClient(dev, timeout=30.0) as c:
        for s in c.services:
            print(f"\nservice {s.uuid}  {s.description}")
            if s.uuid.startswith(SKIP_SERVICES):
                print("   (OTA service: not read)")
                continue
            for ch in s.characteristics:
                line = f"  char {ch.uuid} [{','.join(ch.properties)}]  {ch.description}"
                if "read" in ch.properties:
                    try:
                        line += f"\n     value: {show(bytes(await c.read_gatt_char(ch)))}"
                    except Exception as e:
                        line += f"\n     read failed: {e!r}"
                print(line)
                for d in ch.descriptors:
                    try:
                        print(f"     desc {d.uuid}: {show(bytes(await c.read_gatt_descriptor(d.handle)))}")
                    except Exception as e:
                        print(f"     desc {d.uuid}: read failed {e!r}")


asyncio.run(main())
