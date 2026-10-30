import asyncio
from bleak import BleakScanner

async def main():
    found = await BleakScanner.discover(timeout=40.0, return_adv=True)
    rows = []
    for addr, (dev, adv) in found.items():
        name = adv.local_name or dev.name or ""
        uuids = ",".join(u[4:8] for u in adv.service_uuids)
        mfr = ",".join(f"0x{k:04X}:{v.hex()[:16]}" for k, v in adv.manufacturer_data.items())
        rows.append((not name, -adv.rssi, addr, name, adv.rssi, uuids, mfr))
    rows.sort()
    print(f"{len(rows)} devices")
    for _, _, addr, name, rssi, uuids, mfr in rows:
        print(f"{addr}  {rssi:>4}dBm  {name:<28} uuids=[{uuids}] mfr=[{mfr}]")

asyncio.run(main())
