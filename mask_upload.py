"""Upload a custom image to a Shining Mask (Megoo) LED mask over BLE.

Protocol per GoneUp/mask-go: 16-row column bitmap + one RGB colour per column,
sent via DATS / data chunks (REOK) / DATCP, then shown in steady mode.

  python mask_upload.py --scan
  python mask_upload.py image.png [--width 44] [--brightness 60] [--preview]
"""
import argparse
import asyncio
import sys

from bleak import BleakClient, BleakScanner
from Crypto.Cipher import AES
from PIL import Image

KEY = bytes.fromhex("32672f7974ad43451d9c6c894a0e8764")
SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
CMD_CHAR = "d44bc439-abfd-45a2-b575-925416129600"
NOTIFY_CHAR = "d44bc439-abfd-45a2-b575-925416129601"
UPLOAD_CHAR = "d44bc439-abfd-45a2-b575-92541612960a"
ROWS = 16
CHUNK = 98


def cmd(name: str, *args: int) -> bytes:
    body = name.encode() + bytes(args)
    pkt = (bytes([len(body)]) + body).ljust(16, b"\x00")
    return AES.new(KEY, AES.MODE_ECB).encrypt(pkt)


def decode_notify(data: bytes) -> str:
    plain = AES.new(KEY, AES.MODE_ECB).decrypt(bytes(data))
    return plain[1 : plain[0] + 1].decode(errors="replace")


def encode_image(path: str, width: int, threshold: int):
    """Return (bitmap, colours, preview_rows). The mask only supports one colour per column."""
    img = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
    img = Image.alpha_composite(bg, img).convert("RGB")
    img = img.resize((width, ROWS), Image.LANCZOS)
    px = img.load()

    bitmap, colours, preview = bytearray(), bytearray(), [[" "] * width for _ in range(ROWS)]
    for x in range(width):
        val, lit = 0, []
        for y in range(ROWS):
            r, g, b = px[x, y]
            if max(r, g, b) > threshold:
                val |= (0x80 >> y) if y < 8 else (0x8000 >> (y - 8))
                lit.append((r, g, b))
                preview[y][x] = "#"
        bitmap += val.to_bytes(2, "little")
        if lit:
            # brightest-normalised average colour of the lit pixels
            avg = [sum(c[i] for c in lit) // len(lit) for i in range(3)]
            peak = max(avg) or 1
            colours += bytes(min(255, v * 255 // peak) for v in avg)
        else:
            colours += b"\x00\x00\x00"
    return bytes(bitmap), bytes(colours), ["".join(r) for r in preview]


async def find_mask(timeout: float = 45.0):
    """Return (device, name, rssi) as soon as the mask is heard, or None."""
    seen = {}

    def match(dev, adv):
        name = adv.local_name or dev.name or ""
        if name.upper().startswith("MASK") or SERVICE in adv.service_uuids:
            seen["hit"] = (dev, name, adv.rssi)
            return True
        return False

    dev = await BleakScanner.find_device_by_filter(match, timeout=timeout)
    return seen.get("hit") if dev else None


async def upload(path: str, width: int, brightness: int, threshold: int):
    bitmap, colours, _ = encode_image(path, width, threshold)
    hit = await find_mask()
    if not hit:
        sys.exit("Mask not found. Is it on, near the PC, and disconnected from the phone app?")
    dev, name, rssi = hit
    print(f"Found {name or '?'} {dev.address} ({rssi} dBm), connecting...")

    queue: asyncio.Queue[str] = asyncio.Queue()

    async def expect(prefix: str, timeout: float = 5.0):
        while True:
            msg = await asyncio.wait_for(queue.get(), timeout)
            if msg.startswith(prefix):
                return
            print(f"  (ignoring notify {msg!r})")

    client = BleakClient(dev, timeout=30.0)
    for attempt in range(1, 4):
        try:
            await client.connect()
            break
        except Exception as e:
            print(f"  connect attempt {attempt} failed: {type(e).__name__} {e}")
            if attempt == 3:
                sys.exit("Could not connect; move the mask closer to the Bluetooth radio.")
            await asyncio.sleep(2)
    print("Connected.")

    try:
        await client.start_notify(NOTIFY_CHAR, lambda _, d: queue.put_nowait(decode_notify(d)))

        async def send(pkt: bytes):
            await client.write_gatt_char(CMD_CHAR, pkt, response=False)

        payload = bitmap + colours
        await send(cmd("DATS", *len(payload).to_bytes(2, "big"), *len(bitmap).to_bytes(2, "big"), 0))
        await expect("DATSOK")

        for idx, off in enumerate(range(0, len(payload), CHUNK)):
            part = payload[off : off + CHUNK]
            await client.write_gatt_char(UPLOAD_CHAR, bytes([len(part) + 1, idx & 0xFF]) + part, response=False)
            await expect("REOK")

        await send(cmd("DATCP"))
        await expect("DATCPOK")

        await send(cmd("MODE", 1))  # steady, no scrolling
        await send(cmd("LIGHT", brightness))
        await asyncio.sleep(0.5)
    finally:
        await client.disconnect()
    print(f"Uploaded {len(payload)} bytes ({width} columns).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?")
    ap.add_argument("--scan", action="store_true", help="just look for the mask")
    ap.add_argument("--width", type=int, default=44, help="columns to render (mask width)")
    ap.add_argument("--brightness", type=int, default=60)
    ap.add_argument("--threshold", type=int, default=40, help="0-255; pixels brighter than this light up")
    ap.add_argument("--preview", action="store_true", help="print ASCII preview, don't upload")
    a = ap.parse_args()

    if a.scan:
        hit = asyncio.run(find_mask(30.0))
        print(f"Found {hit[1]} {hit[0].address} ({hit[2]} dBm)" if hit else "Mask not found.")
        return
    if not a.image:
        ap.error("image path required")
    if a.preview:
        print("\n".join(encode_image(a.image, a.width, a.threshold)[2]))
        return
    asyncio.run(upload(a.image, a.width, a.brightness, a.threshold))


if __name__ == "__main__":
    main()
