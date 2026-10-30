"""Async client for the mask: one persistent connection, auto-reconnect, one op at a time.

    async with Mask() as m:
        await m.upload_diy("face.png", slot=1)      # full 46x58 panel, shown at once
        await m.show_text("HELLO", "00ff00")        # 16-row text path
        await m.set_brightness(40)
        await m.next_show()                         # shows registry (maskapi.shows)

Every public method takes the lock, connects if needed, and on TransportError or an
ack timeout retries the whole operation with the recipe verified on the mask: drain
stale acks, wait 1.5 s, reconnect only if the link actually dropped (4 attempts).
Multi-frame uploads retry per frame.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Awaitable, Callable, List, Optional, Sequence, TypeVar

from . import protocol as P
from .image import (MASK_WIDTH, Frame, ImageSource, Panel, collect_frames, encode_image, encode_panel,
                    encode_pixels, load_frames, parse_color, render_text)
from .shows import ShowRegistry
from .transport import BleakTransport, Transport, TransportError

log = logging.getLogger("maskapi")
T = TypeVar("T")
Progress = Optional[Callable[[int, int], None]]


class MaskError(Exception):
    """The mask answered ERROR or something unexpected (not retried)."""


class AckTimeout(MaskError):
    """Expected notification never came (retried)."""


class Mask:
    def __init__(self, transport: Optional[Transport] = None, address: Optional[str] = None,
                 retries: int = 4, ack_timeout: float = 5.0, gap: float = 0.05,
                 idle_disconnect: float = 0.0, retry_wait: float = 1.5, play_gap: float = 0.1,
                 shows=None, panel_map=None):
        """shows: a ShowRegistry or a path to its JSON (default ~/.maskapi/shows.json, lazily).
        panel_map: a maskapi.panel.PanelMap for avoid_eyes (default: eyes.json)."""
        self.transport = transport or BleakTransport(address)
        self.retries = retries
        self.ack_timeout = ack_timeout
        self.gap = gap
        self.retry_wait = retry_wait
        self.play_gap = play_gap  # pause between the two packets of a long PLAY
        self.idle_disconnect = idle_disconnect
        self._shows = shows if shows is None or isinstance(shows, ShowRegistry) else ShowRegistry(shows)
        self.panel_map = panel_map
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

    @property
    def shows(self) -> ShowRegistry:
        if self._shows is None:
            self._shows = ShowRegistry()
        return self._shows

    def status(self) -> dict:
        t = self.transport
        return {
            "connected": self.connected, "name": t.name, "address": t.address, "rssi": t.rssi,
            "show": self._shows.current if self._shows else None,
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

    async def _run(self, op: Callable[[], Awaitable[T]], retries: Optional[int] = None) -> T:
        """Run op under the lock with connect + retry. On a link failure or missing ack:
        drain the notify queue, wait retry_wait, reconnect only if disconnected, retry."""
        retries = retries or self.retries
        async with self._lock:
            if self._idle_task:
                self._idle_task.cancel()
            try:
                for attempt in range(1, retries + 1):
                    try:
                        await self._ensure()
                        result = await op()
                        self.last_error = None
                        return result
                    except (TransportError, AckTimeout) as e:
                        self.last_error = f"{type(e).__name__}: {e}"
                        log.warning("attempt %d/%d failed: %s", attempt, retries, self.last_error)
                        if attempt == retries:
                            raise
                        self._drain()
                        await asyncio.sleep(self.retry_wait)
                        self._drain()
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

    def _panel(self, src, fit="contain", avoid_eyes=False, margin=1, valign="center") -> Panel:
        return encode_panel(src, fit, avoid_eyes, margin, valign, self.panel_map)

    async def upload_diy(self, src, slot: int = 1, fit: str = "contain", play: bool = True,
                         timestamp: Optional[int] = None, avoid_eyes: bool = False, margin: int = 1,
                         valign: str = "center", delete_first: bool = True) -> Panel:
        """Upload a 46x58 RGB image (path/bytes/PIL/Panel) into DIY slot 1-20, optionally show it.
        avoid_eyes places it with maskapi.panel.place. delete_first sends DELE first
        (one attempt, failure ignored), as diy.py does."""
        panel = self._panel(src, fit, avoid_eyes, margin, valign)
        P.check_slot(slot)
        if delete_first:
            await self.delete_slots([slot], ignore_errors=True)
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
                               play: bool = True, max_frames: int = P.MAX_PLAY, avoid_eyes: bool = False,
                               margin: int = 1, valign: str = "center", delete_first: bool = True,
                               progress: Progress = None) -> list:
        """Upload frames (list of images, or one animated GIF, subsampled to max_frames) to
        consecutive slots, each frame with its own retries, then PLAY them together so the
        mask cycles through them (~1 frame/s). Returns the slots used."""
        if not isinstance(frames, (list, tuple)):
            frames = load_frames(frames, max_frames)
        if not frames:
            raise ValueError("no frames")
        if len(frames) > max_frames:
            raise ValueError(f"at most {max_frames} frames")
        slots = list(range(start_slot, start_slot + len(frames)))
        P.check_slot(start_slot)
        P.check_slot(slots[-1])
        panels = [self._panel(f, fit, avoid_eyes, margin, valign) for f in frames]
        if delete_first:
            await self.delete_slots(slots, ignore_errors=True)
        for i, (p, slot) in enumerate(zip(panels, slots)):
            await self.upload_diy(p, slot, play=False, delete_first=False)
            if progress:
                progress(i + 1, len(slots))
        if play:
            await self.play_slots(slots)
        return slots

    async def _play(self, slots: Sequence[int], wait_ack: bool = True):
        self._drain()
        for i, pkt in enumerate(P.play_packets(slots)):
            if i:
                await asyncio.sleep(self.play_gap)
            await self._write_plain(pkt)
        if wait_ack:
            await self._expect("PLAYOK")

    async def play_slots(self, slots: Sequence[int]):
        """PLAY 1-20 slots: one slot shows it, several make the mask cycle them (~1 s/frame).
        More than 10 uses the long two-packet form."""
        slots = list(slots)
        P.play_packets(slots)  # validate before connecting
        await self._run(lambda: self._play(slots))
        self.state.update(showing="diy", slots=slots)

    async def play_diy_slot(self, slot: int):
        await self.play_slots([slot])

    async def delete_slots(self, slots: Sequence[int], ignore_errors: bool = False):
        """DELE in packets of up to 10 slots, each acked DELEOK. ignore_errors: a single
        attempt, any failure logged and swallowed (used before uploads)."""
        slots = [P.check_slot(s) for s in slots]
        groups = [slots[i : i + P.MAX_PER_PACKET] for i in range(0, len(slots), P.MAX_PER_PACKET)]

        async def op():
            for g in groups:
                self._drain()
                await self._write_plain(P.dele(g))
                await self._expect("DELEOK")
        try:
            await self._run(op, retries=1 if ignore_errors else None)
        except (TransportError, MaskError) as e:
            if not ignore_errors:
                raise
            log.info("DELE %s failed (ignored): %s", slots, e)
            return
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

    # ---- shows -----------------------------------------------------------------------

    def list_shows(self) -> dict:
        return self.shows.to_json()

    async def define_show(self, name: str, frames, start_slot: Optional[int] = None, fit: str = "contain",
                          avoid_eyes: bool = False, margin: int = 1, valign: str = "center",
                          play: bool = True, force: bool = False, delete_first: bool = True,
                          progress: Progress = None) -> dict:
        """Upload frames (images, GIFs, frame dirs; <=20) to consecutive slots and record
        them as show `name`. start_slot None: the show's current slots, else the first free
        run. Overlapping another show raises ShowError unless force (that show is dropped)."""
        frames = collect_frames(frames, P.MAX_PLAY)
        slots = self.shows.plan(name, len(frames), start_slot, force)
        await self.upload_animation(frames, slots[0], fit, play=False, avoid_eyes=avoid_eyes, margin=margin,
                                    valign=valign, delete_first=delete_first, progress=progress)
        dropped = self.shows.define(name, slots, force=force, frames=len(frames), avoid_eyes=avoid_eyes)
        if play:
            await self.play_show(name)
        return {"name": name, "slots": slots, "dropped": dropped}

    async def play_show(self, name: str) -> List[int]:
        slots = self.shows.slots(name)
        await self.play_slots(slots)
        self.shows.set_current(name)
        self.state["show"] = name
        return slots

    async def next_show(self) -> str:
        """Play the show after the current one in the registry order (like a hand wave,
        but animations keep animating). Persists the new current show."""
        name = self.shows.next_name()
        await self.play_show(name)
        return name

    async def gesture_our_shows(self, loop_random: bool = False) -> List[int]:
        """FACE in DIY mode over the first slot of each show, in cycle order. The gesture
        shows each slot as a still; animations don't animate this way."""
        ids = self.shows.gesture_ids()
        await self.set_gesture(True, loop_random, P.SELECT_DIY, ids)
        return ids

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
        """SPEED v: only slows slot playback (1 ~3.5 s/frame; 10-255 ~1.0 s/frame)."""
        await self._simple("speed", speed, "SPEED", speed)

    async def show_builtin_image(self, idx: int, *extra: int):
        """IMAG idx [extra...]. `06 IMAG 01 00` (idx 1, extra 0) worked on the mask."""
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
        """FACE: hand-wave sensor cycles images. select 0 all, 1 built-in ids, 2 DIY slots.
        Enabling is acked FACEOK (verified); disabling got no reply, so it isn't awaited."""
        plain = P.face(enable, loop_random, select, ids)
        await self.send_raw(plain, expect="FACEOK" if enable else None)
        self.state["gesture"] = {"enable": bool(enable), "random": bool(loop_random),
                                 "select": select, "ids": list(ids) if select else []}

    async def send_rhythm(self, mode: int, bars: Sequence[int]):
        """One mic/rhythm frame (24 bars, 0-9) to the music char; stream every ~120 ms."""
        plain = P.rhythm(mode, bars)
        await self._run(lambda: self.transport.write(P.MUSIC_CHAR, P.encrypt(plain)))

    # ---- fast paths for streaming (no ack wait) --------------------------------------

    async def play_nowait(self, slots: Sequence[int]):
        """PLAY without waiting for PLAYOK (streaming fast path). A link failure still raises."""
        slots = list(slots)
        P.play_packets(slots)
        await self._run(lambda: self._play(slots, wait_ack=False), retries=1)
        self.state.update(showing="diy", slots=slots)
