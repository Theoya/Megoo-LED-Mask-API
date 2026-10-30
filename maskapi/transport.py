"""BLE transport behind a tiny interface, so the client can be tested with a fake.

A transport connects, writes bytes to a characteristic UUID and delivers raw
notification bytes to a callback. Any link-level failure is raised as TransportError.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from .protocol import NAME_PREFIX, NOTIFY_CHAR, SERVICE

log = logging.getLogger("maskapi")
NotifyCb = Callable[[bytes], None]


class TransportError(Exception):
    """Connect/GATT failure; the client reconnects and retries on this."""


class Transport:
    name: Optional[str] = None
    address: Optional[str] = None
    rssi: Optional[int] = None

    @property
    def is_connected(self) -> bool:
        raise NotImplementedError

    async def connect(self, on_notify: NotifyCb, on_disconnect: Callable[[], None]) -> None:
        raise NotImplementedError

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def write(self, char: str, data: bytes) -> None:
        raise NotImplementedError


async def scan(timeout: float = 20.0, prefix: str = NAME_PREFIX):
    """Return [(BLEDevice, name, rssi)] of masks seen, strongest first."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    hits = []
    for dev, adv in found.values():
        name = adv.local_name or dev.name or ""
        if name.upper().startswith(prefix) or SERVICE in adv.service_uuids:
            hits.append((dev, name, adv.rssi))
    return sorted(hits, key=lambda h: -(h[2] or -999))


class BleakTransport(Transport):
    """Real BLE via bleak. address=None scans for the first device named MASK*."""

    def __init__(self, address: Optional[str] = None, scan_timeout: float = 20.0,
                 connect_timeout: float = 30.0, connect_attempts: int = 3):
        self.address = address
        self.scan_timeout = scan_timeout
        self.connect_timeout = connect_timeout
        self.connect_attempts = connect_attempts
        self._device = None
        self._client = None

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def _find(self):
        from bleak import BleakScanner

        if self.address:
            dev = await BleakScanner.find_device_by_address(self.address, timeout=self.scan_timeout)
            if dev:
                self.name = dev.name
            return dev or self.address  # BlueZ may still know it
        hits = await scan(self.scan_timeout)
        if not hits:
            raise TransportError("mask not found (on, in range, phone app disconnected?)")
        dev, self.name, self.rssi = hits[0]
        self.address = dev.address
        return dev

    async def connect(self, on_notify, on_disconnect):
        from bleak import BleakClient

        last = None
        for attempt in range(1, self.connect_attempts + 1):
            try:
                if self._device is None:
                    self._device = await self._find()
                client = BleakClient(self._device, timeout=self.connect_timeout,
                                     disconnected_callback=lambda _: on_disconnect())
                await client.connect()
                await client.start_notify(NOTIFY_CHAR, lambda _, d: on_notify(bytes(d)))
                self._client = client
                log.info("connected to %s %s", self.name, self.address)
                return
            except Exception as e:  # BleakError, BleakDBusError, TimeoutError, OSError...
                last = e
                log.warning("connect attempt %d failed: %s %s", attempt, type(e).__name__, e)
                if attempt >= 2:
                    self._device = None  # rescan next time
                await asyncio.sleep(2)
        raise TransportError(f"could not connect after {self.connect_attempts} attempts: {last!r}")

    async def disconnect(self):
        client, self._client = self._client, None
        if client:
            try:
                await client.disconnect()
            except Exception as e:
                log.debug("disconnect error: %r", e)

    async def write(self, char, data):
        if not self.is_connected:
            raise TransportError("not connected")
        try:
            await self._client.write_gatt_char(char, data, response=False)
        except Exception as e:
            raise TransportError(f"write failed: {e!r}") from e
