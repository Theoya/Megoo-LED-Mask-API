"""Low-latency streaming on top of Mask: latest-wins lanes for slot switching, rhythm
bars, full-panel frames and 16-row band frames.

    async with Streamer(mask, on_event=print) as s:
        await s.handle({"type": "slot", "slot": 3})
        await s.handle({"type": "rhythm", "mode": 1, "bars": [9] * 24})
        await s.handle(png_bytes)                   # binary = full-panel image

Each lane keeps only the newest pending item: while one send is in flight, newer
items replace each other (counted as `dropped`) and only the latest is sent next.
Lanes share the Mask lock, so BLE operations never interleave.

  play   {"type":"slot","slot":n} / {"type":"show","name":...}  PLAY without waiting for PLAYOK
  rhythm {"type":"rhythm","mode":0-4,"bars":[24 x 0-9]}         write to ...960b, >= rhythm_interval apart
  image  {"type":"image","png_b64":...} or binary PNG           upload to scratch_slot, then PLAY it
  band   {"type":"band","png_b64":...}                          16-row text-channel upload + MODE 1

The rate limits are configurable because they are not measured on hardware yet (TBD).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from . import protocol as P
from .image import encode_image, encode_panel

log = logging.getLogger("maskapi")
Event = Callable[[dict], Any]


@dataclass
class StreamConfig:
    scratch_slot: int = 20        # DIY slot overwritten by streamed images
    rhythm_interval: float = 0.12  # min seconds between rhythm writes (app uses ~120 ms)
    play_interval: float = 0.0    # min seconds between PLAY switches (TBD on hardware)
    image_interval: float = 0.0   # min seconds between image uploads (each takes ~4-8 s anyway)
    band_interval: float = 0.0    # min seconds between band uploads (TBD)
    stats_interval: float = 2.0   # seconds between stats events (0 = never)

    def __post_init__(self):
        P.check_slot(self.scratch_slot)
        for k in ("rhythm_interval", "play_interval", "image_interval", "band_interval", "stats_interval"):
            if getattr(self, k) < 0:
                raise ValueError(f"{k} must be >= 0")


@dataclass
class Lane:
    name: str
    interval: float
    send: Callable[[Any], Awaitable[None]]
    pending: Any = None
    received: int = 0
    sent: int = 0
    dropped: int = 0
    errors: int = 0
    last_seconds: Optional[float] = None  # duration of the last send
    times: collections.deque = field(default_factory=lambda: collections.deque(maxlen=20))
    event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    busy: bool = False

    def fps(self, now: float, window: float = 10.0) -> float:
        ts = [t for t in self.times if now - t <= window]
        if len(ts) < 2:
            return 0.0
        return round((len(ts) - 1) / (ts[-1] - ts[0]), 3) if ts[-1] > ts[0] else 0.0

    def stats(self, now: float) -> dict:
        return {"received": self.received, "sent": self.sent, "dropped": self.dropped, "errors": self.errors,
                "fps": self.fps(now), "last_seconds": self.last_seconds, "busy": self.busy,
                "pending": self.pending is not None}


def _b64(s: str) -> bytes:
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"bad base64: {e}") from None


class Streamer:
    def __init__(self, mask, config: Optional[StreamConfig] = None, on_event: Optional[Event] = None, **kw):
        self.mask = mask
        self.config = config or StreamConfig(**kw)
        self.on_event = on_event
        c = self.config
        self.lanes: Dict[str, Lane] = {
            "play": Lane("play", c.play_interval, self._send_play),
            "rhythm": Lane("rhythm", c.rhythm_interval, self._send_rhythm),
            "image": Lane("image", c.image_interval, self._send_image),
            "band": Lane("band", c.band_interval, self._send_band),
        }
        self._stats_task: Optional[asyncio.Task] = None
        self._activity = False
        self.started = time.monotonic()

    async def __aenter__(self):
        self.start()
        return self

    async def __aexit__(self, *exc):
        await self.stop()

    def start(self):
        for lane in self.lanes.values():
            if lane.task is None:
                lane.task = asyncio.ensure_future(self._loop(lane))
        if self.config.stats_interval and self.on_event and self._stats_task is None:
            self._stats_task = asyncio.ensure_future(self._stats_loop())

    async def stop(self):
        tasks = [lane.task for lane in self.lanes.values() if lane.task] + ([self._stats_task] if self._stats_task else [])
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for lane in self.lanes.values():
            lane.task = None
        self._stats_task = None

    async def drain(self, timeout: float = 30.0):
        """Wait until every lane is idle with nothing pending (tests / clean shutdown)."""
        deadline = time.monotonic() + timeout
        while any(l.pending is not None or l.busy for l in self.lanes.values()):
            if time.monotonic() > deadline:
                raise asyncio.TimeoutError("stream lanes still busy")
            await asyncio.sleep(0.01)

    def stats(self) -> dict:
        now = time.monotonic()
        return {"type": "stats", "uptime": round(now - self.started, 1),
                "lanes": {n: l.stats(now) for n, l in self.lanes.items()}}

    # ---- input --------------------------------------------------------------------

    def submit(self, lane_name: str, item) -> bool:
        """Queue item on a lane (latest wins). Returns True if it replaced a pending item."""
        lane = self.lanes[lane_name]
        lane.received += 1
        replaced = lane.pending is not None
        if replaced:
            lane.dropped += 1
        lane.pending = item
        lane.event.set()
        self._activity = True
        return replaced

    async def handle(self, msg: Union[dict, bytes, bytearray]) -> dict:
        """Validate one message, queue it, and return the ack/error reply."""
        ref = {}
        try:
            if isinstance(msg, (bytes, bytearray)):
                msg = {"type": "image", "png": bytes(msg)}
            if not isinstance(msg, dict):
                raise ValueError("message must be a JSON object")
            if "id" in msg:
                ref["id"] = msg["id"]
            kind = msg.get("type")
            ref["for"] = kind
            if kind == "stats":
                return {**self.stats(), **ref}
            lane, item = self._parse(kind, msg)
            replaced = self.submit(lane, item)
            return {"type": "ack", **ref, "lane": lane, "replaced_pending": replaced}
        except (ValueError, KeyError, TypeError) as e:
            return {"type": "error", **ref, "error": str(e)}

    def _parse(self, kind, msg):
        if kind == "slot":
            slot = msg["slot"]
            if not isinstance(slot, int):
                raise ValueError("slot must be an int")
            P.check_slot(slot)
            return "play", {"slots": [slot]}
        if kind == "show":
            name = msg["name"]
            return "play", {"slots": self.mask.shows.slots(name), "show": name}
        if kind == "rhythm":
            plain = P.rhythm(int(msg.get("mode", 0)), msg.get("bars") or [])
            return "rhythm", plain
        if kind in ("image", "band"):
            data = msg.get("png") or _b64(msg.get("png_b64") or "")
            if not data:
                raise ValueError("png_b64 required")
            opts = {k: msg[k] for k in ("fit", "avoid_eyes", "margin", "valign", "threshold", "width") if k in msg}
            return kind, {"data": data, **opts}
        raise ValueError(f"unknown message type {kind!r} (slot, show, rhythm, image, band, stats)")

    # ---- lanes --------------------------------------------------------------------

    async def _loop(self, lane: Lane):
        last = 0.0
        while True:
            await lane.event.wait()
            lane.event.clear()
            if lane.pending is None:
                continue
            lane.busy = True
            try:
                wait = lane.interval - (time.monotonic() - last)
                if wait > 0:
                    await asyncio.sleep(wait)  # newer items keep replacing pending meanwhile
                item, lane.pending = lane.pending, None
                t0 = time.monotonic()
                try:
                    await lane.send(item)
                    lane.sent += 1
                    lane.times.append(time.monotonic())
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    lane.errors += 1
                    log.warning("stream %s failed: %s: %s", lane.name, type(e).__name__, e)
                    self._emit({"type": "error", "lane": lane.name, "error": f"{type(e).__name__}: {e}"})
                finally:
                    last = time.monotonic()
                    lane.last_seconds = round(last - t0, 3)
            finally:
                lane.busy = False
                if lane.pending is not None:
                    lane.event.set()

    async def _send_play(self, item):
        await self.mask.play_nowait(item["slots"])
        if item.get("show"):
            self.mask.shows.set_current(item["show"])

    async def _send_rhythm(self, plain: bytes):
        await self.mask._run(lambda: self.mask.transport.write(P.MUSIC_CHAR, P.encrypt(plain)), retries=1)

    async def _send_image(self, item):
        panel = await asyncio.to_thread(encode_panel, item["data"], item.get("fit", "contain"),
                                        bool(item.get("avoid_eyes", False)), int(item.get("margin", 1)),
                                        item.get("valign", "center"), self.mask.panel_map)
        await self.mask.upload_diy(panel, self.config.scratch_slot, play=True, delete_first=False)

    async def _send_band(self, item):
        frame = await asyncio.to_thread(encode_image, item["data"], int(item.get("width", 0)) or None,
                                        int(item.get("threshold", 40)))
        await self.mask.upload_frame(frame, P.MODE_STEADY)

    # ---- events -------------------------------------------------------------------

    def _emit(self, ev: dict):
        if self.on_event:
            try:
                r = self.on_event(ev)
                if asyncio.iscoroutine(r):
                    asyncio.ensure_future(r)
            except Exception as e:  # never let a broken listener kill a lane
                log.debug("stream event listener failed: %r", e)

    async def _stats_loop(self):
        while True:
            await asyncio.sleep(self.config.stats_interval)
            if self._activity or any(l.busy or l.pending is not None for l in self.lanes.values()):
                self._activity = False
                self._emit(self.stats())
