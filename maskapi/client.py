"""Async client for the mask: one persistent connection, auto-reconnect, one op at a time.

    async with Mask() as m:
        await m.upload_diy("face.png", slot=1)      # full 46x58 panel, shown at once
        await m.show_text("HELLO", "00ff00")        # 16-row text path
        await m.set_brightness(40)

Every public method takes the lock, connects if needed, and on TransportError or an
ack timeout drops the link, reconnects and retries the whole operation.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Awaitable, Callable, Optional, Sequence, TypeVar

from . import protocol as P
from .image import (MASK_WIDTH, Frame, ImageSource, Panel, encode_image, encode_panel, encode_pixels,
                    load_frames, parse_color, render_text)
from .transport import BleakTransport, Transport, TransportError

log = logging.getLogger("maskapi")
T = TypeVar("T")


class MaskError(Exception):
    """The mask answered ERROR or something unexpected (not retried)."""


class AckTimeout(MaskError):
    """Expected notification never came (retried after reconnect)."""


class Mask:
    def __init__(self, transport: Optional[Transport] = None, address: Optional[str] = None,
                 retries: int = 3, ack_timeout: float = 5.0, gap: float = 0.05,
                 idle_disconnect: float = 0.0):
        self.transport = transport or BleakTransport(address)
        self.retries = retries
        self.ack_timeout = ack_timeout
        self.gap = gap
        self.idle_disconnect = idle_disconnect
        self.notifications = collections.deque(maxlen=50)  # (time, text)
        self.sent = collections.deque(maxlen=50)  # (time, description)
        self.state = {}  # last values we set: brightness, mode, slots, ...
        self.last_error: Optional[str] = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._idle_task: Optional[asyncio.Task] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    @property
    def connected(self) -> bool:
        return self.transport.is_connected

    def status(self) -> dict:
        t = self.transport
        return {
            "connected": self.connected, "name": t.name, "address": t.address, "rssi": t.rssi,
            "state": dict(self.state), "last_error": self.last_error,
            "notifications": [n for _, n in list(self.notifications)[-10:]],
            "sent": [s for _, s in list(self.sent)[-10:]],
        }

    # ---- connection -------------------------------------------------------------

    def _on_notify(self, data: bytes):
        try:
            body = P.notify_body(data)
        except Exception:
            body = b"?" + bytes(data)
        self.notifications.append((time.time(), P.fmt(body)))
        self._queue.put_nowait(body)

    def _on_disconnect(self):
        log.info("mask disconnected")

    async def _ensure(self):
        if not self.transport.is_connected:
            await self.transport.connect(self._on_notify, self._on_disconnect)

    async def connect(self):
        async with self._lock:
            await self._ensure()

    async def disconnect(self):
        async with self._lock:
            await self.transport.disconnect()

    async def _run(self, op: Callable[[], Awaitable[T]]) -> T:
        """Run op under the lock with connect + retry-on-link-failure."""
        async with self._lock:
            if self._idle_task:
                self._idle_task.cancel()
            try:
                for attempt in range(1, self.retries + 1):
                    try:
                        await self._ensure()
                        result = await op()
                        self.last_error = None
                        return result
                    except (TransportError, AckTimeout) as e:
                        self.last_error = f"{type(e).__name__}: {e}"
                        log.warning("attempt %d/%d failed: %s", attempt, self.retries, self.last_error)
                        await self.transport.disconnect()
                        if attempt == self.retries:
                            raise
                        await asyncio.sleep(1)
                    except MaskError as e:
                        self.last_error = f"{type(e).__name__}: {e}"
                        raise
            finally:
                if self.idle_disconnect:
                    self._idle_task = asyncio.ensure_future(self._idle())

    async def _idle(self):
        await asyncio.sleep(self.idle_disconnect)
        async with self._lock:
            await self.transport.disconnect()

    # ---- primitives (call only inside _run) ----------------------------------------

    def _drain(self):
        while not self._queue.empty():
            self._queue.get_nowait()

    async def _write_plain(self, plain: bytes, char: str = P.CMD_CHAR):
        self.sent.append((time.time(), P.describe(plain)))
        await self.transport.write(char, P.encrypt(plain))
        if self.gap:
            await asyncio.sleep(self.gap)

    async def _expect(self, prefix: str, timeout: Optional[float] = None) -> bytes:
        """Wait for a notification starting with prefix; ERROR raises MaskError."""
        timeout = timeout or self.ack_timeout
        deadline, want = time.monotonic() + timeout, prefix.encode()
        while True:
            try:
                body = await asyncio.wait_for(self._queue.get(), max(0.0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                raise AckTimeout(f"no {prefix!r} notification within {timeout}s") from None
            if body.startswith(want):
                return body
            if body.startswith(b"ERROR"):
                raise MaskError(f"mask replied {P.fmt(body)!r} while waiting for {prefix}")
            log.debug("ignoring notify %r while waiting for %s", body, prefix)

    async def _collect(self, wait: float) -> list:
        out, deadline = [], time.monotonic() + wait
        while (left := deadline - time.monotonic()) > 0:
            try:
                out.append(P.fmt(await asyncio.wait_for(self._queue.get(), left)))
            except asyncio.TimeoutError:
                break
        return out

    async def _upload(self, payload: bytes, dats: bytes, datcp: bytes, chunk: int = P.CHUNK):
        self._drain()
        await self._write_plain(dats)
        await self._expect("DATSOK")
        for pkt in P.chunks(payload, chunk):
            await self.transport.write(P.UPLOAD_CHAR, pkt)
            await self._expect("REOK")
        await self._write_plain(datcp)
        await self._expect("DATCPOK")

    async def _send_all(self, cmds):
        for c in cmds or []:
            if isinstance(c, (bytes, bytearray)):
                await self._write_plain(P.pad(c))
            else:
                await self._write_plain(P.plaintext(c[0], *c[1:]))

    # ---- raw / generic ------------------------------------------------------------

    async def send_raw(self, plain: bytes, expect: Optional[str] = None, wait: float = 0.0,
                       char: str = P.CMD_CHAR) -> list:
        """Send arbitrary plaintext (padded to 16, encrypted as-is). Returns notifications
        received: the matching ack if expect is set, else whatever arrives within wait seconds."""
        plain = P.pad(plain)

        async def op():
            self._drain()
            await self._write_plain(plain, char)
            if expect:
                return [P.fmt(await self._expect(expect))]
            return await self._collect(wait) if wait else []
        return await self._run(op)

    async def send_command(self, name: str, *args: P.Arg, expect: Optional[str] = None,
                           wait: float = 0.0) -> list:
        return await self.send_raw(P.plaintext(name, *args), expect=expect, wait=wait)

    async def write_char(self, char: str, data: bytes):
        """Write bytes unmodified to any characteristic (reverse-engineering hook)."""
        await self._run(lambda: self.transport.write(char, bytes(data)))

    async def upload_generic(self, payload: bytes, dats: bytes, datcp: bytes, after=None,
                             chunk: int = P.CHUNK):
        """DATS/chunks/DATCP with caller-built DATS and DATCP plaintexts, then send `after`
        (list of (name, *args) tuples or raw plaintext bytes)."""
        async def op():
            await self._upload(payload, dats, datcp, chunk)
            await self._send_all(after)
        await self._run(op)

    async def upload(self, payload: bytes, bitmap_len: int, flag: int = 0, after=None,
                     chunk: int = P.CHUNK):
        """16-row text/bitmap style upload: DATS [total][bitmap_len][flag] ... plain DATCP."""
        await self.upload_generic(payload, P.dats_text(len(payload), bitmap_len, flag), P.datcp(), after, chunk)

    # ---- 16-row text / bitmap ------------------------------------------------------

    async def upload_frame(self, frame: Frame, mode: int = P.MODE_STEADY,
                           brightness: Optional[int] = None, speed: Optional[int] = None):
        after = [("MODE", mode)]
        if speed is not None:
            after.append(("SPEED", speed))
        if brightness is not None:
            after.append(("LIGHT", brightness))
        await self.upload(frame.payload, len(frame.bitmap), 0, after)
        self.state.pop("text", None)
        self.state.update(showing="bitmap", mode=mode, width=frame.width)
        if speed is not None:
            self.state["speed"] = speed
        if brightness is not None:
            self.state["brightness"] = brightness

    async def upload_image(self, src: ImageSource, width: Optional[int] = MASK_WIDTH,
                           threshold: int = 40, brightness: Optional[int] = None,
                           mode: int = P.MODE_STEADY) -> Frame:
        """16-row, one-colour-per-column image (the mask_upload.py path)."""
        frame = encode_image(src, width, threshold)
        await self.upload_frame(frame, mode, brightness)
        return frame

    async def show_text(self, text: str, color="ff4600", mode: Optional[int] = None,
                        speed: Optional[int] = None, brightness: Optional[int] = None,
                        font_path: Optional[str] = None, font_size: Optional[int] = None,
                        rainbow: bool = False) -> Frame:
        """Render text to a 16-row bitmap and upload it. mode defaults to scroll-left
        when the text is wider than the mask, else steady."""
        frame = encode_pixels(render_text(text, color, font_path, font_size, rainbow=rainbow), 0)
        if mode is None:
            mode = P.MODE_SCROLL_LEFT if frame.width > MASK_WIDTH else P.MODE_STEADY
        await self.upload_frame(frame, mode, brightness, speed)
        self.state["text"] = text
        return frame

    # ---- full-panel DIY slots ------------------------------------------------------

    async def upload_diy(self, src, slot: int = 1, fit: str = "contain", play: bool = True,
                         timestamp: Optional[int] = None) -> Panel:
        """Upload a 46x58 RGB image (path/bytes/PIL/Panel) into DIY slot 1-20, optionally show it."""
        panel = src if isinstance(src, Panel) else encode_panel(src, fit)
        P.check_slot(slot)
        payload = panel.payload

        async def op():
            await self._upload(payload, P.dats_diy(len(payload), slot), P.datcp_diy(timestamp))
            if play:
                await self._play([slot])
        await self._run(op)
        self.state.setdefault("diy_uploaded", [])
        if slot not in self.state["diy_uploaded"]:
            self.state["diy_uploaded"] = sorted(self.state["diy_uploaded"] + [slot])
        if play:
            self.state.update(showing="diy", slots=[slot])
        return panel

    async def upload_animation(self, frames, start_slot: int = 1, fit: str = "contain",
                               play: bool = True, max_frames: int = P.MAX_PER_PACKET) -> list:
        """Upload frames (list of images, or one animated GIF) to consecutive slots and PLAY
        them together so the mask cycles through them. Returns the slots used."""
        if not isinstance(frames, (list, tuple)):
            frames = load_frames(frames, max_frames)
        if not frames:
            raise ValueError("no frames")
        if len(frames) > max_frames:
            raise ValueError(f"at most {max_frames} frames")
        slots = list(range(start_slot, start_slot + len(frames)))
        P.check_slot(slots[-1])
        for f, slot in zip(frames, slots):
            await self.upload_diy(f, slot, fit, play=False)
        if play:
            await self.play_slots(slots)
        return slots

    async def _play(self, slots: Sequence[int]):
        self._drain()
        await self._write_plain(P.play(slots))
        await self._expect("PLAYOK")

    async def play_slots(self, slots: Sequence[int]):
        """PLAY n slots...: one slot shows it, several make the mask cycle them (<=10)."""
        slots = list(slots)
        P.play(slots)  # validate before connecting
        await self._run(lambda: self._play(slots))
        self.state.update(showing="diy", slots=slots)

    async def play_diy_slot(self, slot: int):
        await self.play_slots([slot])

    async def delete_slots(self, slots: Sequence[int]):
        """DELE in packets of up to 10 slots, each acked DELEOK."""
        slots = [P.check_slot(s) for s in slots]
        groups = [slots[i : i + P.MAX_PER_PACKET] for i in range(0, len(slots), P.MAX_PER_PACKET)]

        async def op():
            for g in groups:
                self._drain()
                await self._write_plain(P.dele(g))
                await self._expect("DELEOK")
        await self._run(op)
        if "diy_uploaded" in self.state:
            self.state["diy_uploaded"] = [s for s in self.state["diy_uploaded"] if s not in slots]

    async def diy_count(self) -> int:
        """CHEC -> 'CHEC' + count byte: number of stored DIY images."""
        async def op():
            self._drain()
            await self._write_plain(P.plaintext("CHEC"))
            body = await self._expect("CHEC")
            if len(body) < 5:
                raise MaskError(f"short CHEC reply {P.fmt(body)!r}")
            return body[4]
        count = await self._run(op)
        self.state["diy_count"] = count
        return count

    # ---- built-ins, settings, gesture, rhythm ------------------------------------------

    async def _simple(self, key: str, value, name: str, *args: P.Arg, expect: Optional[str] = None):
        await self.send_command(name, *args, expect=expect)
        self.state[key] = value

    async def set_brightness(self, level: int):
        """LIGHT v, v >= 1 per the app."""
        await self._simple("brightness", level, "LIGHT", level)

    async def set_mode(self, mode: int):
        await self._simple("mode", mode, "MODE", mode)

    async def set_speed(self, speed: int):
        await self._simple("speed", speed, "SPEED", speed)

    async def show_builtin_image(self, idx: int, *extra: int):
        """IMAG idx (0-based)."""
        await self._simple("builtin_image", idx, "IMAG", idx, *extra)
        self.state["showing"] = "builtin_image"

    async def show_builtin_animation(self, idx: int, *extra: int):
        """ANIM idx (0-based)."""
        await self._simple("builtin_animation", idx, "ANIM", idx, *extra)
        self.state["showing"] = "builtin_animation"

    async def loop_builtin_animations(self):
        """LOOA: cycle through all built-in animations."""
        await self._simple("showing", "builtin_loop", "LOOA")

    async def set_text_mode(self, enable: bool, mode: int):
        await self._simple("text_mode", [int(enable), mode], "M", int(enable), mode)

    async def set_foreground(self, color, enable: bool = True):
        rgb = parse_color(color)
        await self._simple("foreground", [int(enable), *rgb], "FC", int(enable), *rgb)

    async def set_background(self, color, enable: bool = True):
        rgb = parse_color(color)
        await self._simple("background", [int(enable), *rgb], "BC", int(enable), *rgb)

    async def set_gesture(self, enable: bool, loop_random: bool = False, select: int = P.SELECT_ALL,
                          ids: Sequence[int] = ()):
        """FACE: hand-wave sensor cycles images. select 0 all, 1 built-in ids, 2 DIY slots."""
        plain = P.face(enable, loop_random, select, ids)
        await self.send_raw(plain, expect="FACEOK")
        self.state["gesture"] = {"enable": bool(enable), "random": bool(loop_random),
                                 "select": select, "ids": list(ids) if select else []}

    async def send_rhythm(self, mode: int, bars: Sequence[int]):
        """One mic/rhythm frame (24 bars, 0-9) to the music char; stream every ~120 ms."""
        plain = P.rhythm(mode, bars)
        await self._run(lambda: self.transport.write(P.MUSIC_CHAR, P.encrypt(plain)))
