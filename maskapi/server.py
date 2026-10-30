"""HTTP API that owns the single BLE connection to the mask.

  python -m maskapi serve [--host 127.0.0.1] [--port 8765] [--address 5F:F6:A0:B0:42:F1] [--fake]

Swagger UI at /docs, schema at /openapi.json. See docs/API.md.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import io
import json
import logging
from contextlib import asynccontextmanager
from typing import List, Literal, Optional, Tuple, Type, Union

from fastapi import FastAPI, HTTPException, Path, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from PIL import UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError, model_validator

from . import protocol as P
from .client import AckTimeout, Mask, MaskError
from .image import MASK_WIDTH, encode_image, encode_panel, encode_pixels, load_frames, render_text
from .panel import default_panel
from .shows import ShowError
from .stream import StreamConfig, Streamer
from .transport import TransportError

HEX_COLOR = "^#?[0-9a-fA-F]{6}$"
FIT = Literal["contain", "cover", "stretch"]
VALIGN = Literal["center", "top", "bottom"]
SHOW_NAME = "^[A-Za-z0-9_-]{1,32}$"


def byte(default=..., **kw):
    return Field(default, ge=0, le=255, **kw)


# ---- models ---------------------------------------------------------------------------

class Ok(BaseModel):
    ok: bool = True


class FrameResult(Ok):
    width: int
    bytes: int
    mode: int
    preview: List[str]
    uploaded: bool


class DiyResult(Ok):
    slots: List[int]
    bytes: int
    played: bool
    uploaded: bool
    preview_png_base64: List[str] = Field(description="46x58 PNG of exactly what is (or would be) sent")


class Level(BaseModel):
    level: int = Field(ge=1, le=255, description="LIGHT value (>=1); the stock app uses roughly 1-100")


class ModeReq(BaseModel):
    mode: int = byte(description="1 steady, 2 blink, 3 scroll left, 4 scroll right, 5 steady")


class SpeedReq(BaseModel):
    speed: int = byte()


class ExtraReq(BaseModel):
    extra: List[int] = Field(default_factory=list, description="extra arg bytes appended after idx")


class ColorReq(BaseModel):
    color: str = Field("ff0000", pattern=HEX_COLOR)
    enable: bool = True


class TextModeReq(BaseModel):
    enable: bool = True
    mode: int = byte()


class SlotsReq(BaseModel):
    slots: List[int] = Field(min_length=1, max_length=P.MAX_PLAY, description="DIY slots 1-20 (up to 20)")


class PlaceOpts(BaseModel):
    avoid_eyes: bool = Field(False, description="keep lit pixels off the eye holes (+margin) and outside the oval")
    margin: int = Field(1, ge=0, le=5, description="eye margin in cells")
    valign: VALIGN = "center"


class CountResult(Ok):
    count: int


class GestureReq(BaseModel):
    enable: bool = True
    loop: Literal["order", "random"] = "order"
    select: Literal["all", "builtin", "diy"] = "all"
    ids: List[int] = Field(default_factory=list, max_length=P.FACE_MAX_IDS,
                           description="built-in indices or DIY slots (ignored for select=all)")


class RhythmReq(BaseModel):
    mode: int = Field(0, ge=0, le=4)
    bars: List[int] = Field(max_length=24, description="up to 24 levels 0-9")


class TextReq(BaseModel):
    text: str = Field(max_length=500)
    color: str = Field("ff4600", pattern=HEX_COLOR)
    rainbow: bool = False
    mode: Optional[int] = byte(None, description="default: scroll left if wider than 44, else steady")
    speed: Optional[int] = byte(None)
    brightness: Optional[int] = Field(None, ge=1, le=255)
    font_size: Optional[int] = Field(None, ge=4, le=64)
    dry_run: bool = False


class ImageOpts(BaseModel):
    image_base64: Optional[str] = Field(None, description="JSON body only")
    width: int = Field(MASK_WIDTH, ge=0, le=4000, description="0 keeps aspect ratio")
    threshold: int = byte(40)
    brightness: Optional[int] = Field(None, ge=1, le=255)
    mode: int = byte(P.MODE_STEADY)
    dry_run: bool = False


class DiyOpts(PlaceOpts):
    image_base64: Optional[str] = Field(None, description="JSON body only")
    slot: int = Field(1, ge=1, le=20)
    fit: FIT = "contain"
    play: bool = True
    dry_run: bool = False


class AnimationOpts(PlaceOpts):
    frames_base64: Optional[List[str]] = Field(None, description="JSON body only: frames, or one animated GIF")
    start_slot: int = Field(1, ge=1, le=20)
    fit: FIT = "contain"
    play: bool = True
    max_frames: int = Field(P.MAX_PLAY, ge=1, le=P.MAX_PLAY)
    dry_run: bool = False


class ShowOpts(PlaceOpts):
    frames_base64: Optional[List[str]] = Field(None, description="JSON body only: frames, or one animated GIF")
    start_slot: Optional[int] = Field(None, ge=1, le=20, description="default: the show's slots, else first free run")
    fit: FIT = "contain"
    play: bool = True
    force: bool = Field(False, description="allow overlapping other shows (they are dropped)")
    max_frames: int = Field(P.MAX_PLAY, ge=1, le=P.MAX_PLAY, description="a longer GIF is subsampled")
    dry_run: bool = False


class ShowResult(Ok):
    name: str
    slots: List[int]
    dropped: List[str] = []
    uploaded: bool
    preview_png_base64: List[str] = []


class PlayShowResult(Ok):
    show: str
    slots: List[int]


class OrderReq(BaseModel):
    order: List[str] = Field(min_length=1)


class GestureShowsReq(BaseModel):
    loop: Literal["order", "random"] = "order"


class GestureShowsResult(Ok):
    ids: List[int]


class RawReq(BaseModel):
    name: Optional[str] = Field(None, pattern="^[A-Za-z]{1,14}$", description="command name, e.g. LIGHT")
    args: List[int] = Field(default_factory=list, description="argument bytes")
    hex: Optional[str] = Field(None, description="raw plaintext (<=16 bytes, padded and encrypted as-is)")
    char: Literal["cmd", "music"] = "cmd"
    expect: Optional[str] = Field(None, description="wait for a notification starting with this")
    wait: float = Field(0.3, ge=0, le=10, description="seconds to collect notifications (if no expect)")

    @model_validator(mode="after")
    def one_of(self):
        if (self.name is None) == (self.hex is None):
            raise ValueError("give exactly one of name or hex")
        return self


class RawResult(Ok):
    plaintext: str
    notifications: List[str]


class UploadReq(BaseModel):
    payload_hex: Optional[str] = None
    payload_base64: Optional[str] = None
    kind: Literal["text", "diy"] = "text"
    bitmap_len: int = Field(0, ge=0, le=0xFFFF, description="text kind")
    flag: int = byte(0, description="text kind: last DATS byte")
    slot: int = Field(1, ge=1, le=20, description="diy kind")
    dats_hex: Optional[str] = Field(None, description="override the whole DATS plaintext (<=16 bytes)")
    datcp_hex: Optional[str] = Field(None, description="override the whole DATCP plaintext")
    after: List[List[Union[int, str]]] = Field(default_factory=list,
                                               description='commands after DATCP, e.g. [["MODE", 1]]')

    def payload(self) -> bytes:
        if (self.payload_hex is None) == (self.payload_base64 is None):
            raise ValueError("give exactly one of payload_hex or payload_base64")
        return bytes.fromhex(self.payload_hex) if self.payload_hex is not None else b64(self.payload_base64)


# ---- helpers --------------------------------------------------------------------------

def b64(s: str) -> bytes:
    try:
        return base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(400, f"bad base64: {e}")


def png_b64(img) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def body_spec(model: Type[BaseModel], json_field: str, multi: bool = False) -> dict:
    """openapi_extra documenting both multipart (file field) and JSON (base64) bodies."""
    props = {k: v for k, v in model.model_json_schema()["properties"].items() if k != json_field}
    fileprop = {"type": "string", "format": "binary"}
    props["file"] = {"type": "array", "items": fileprop} if multi else fileprop
    return {"requestBody": {"required": True, "content": {
        "multipart/form-data": {"schema": {"type": "object", "required": ["file"], "properties": props}},
        "application/json": {"schema": model.model_json_schema()},
    }}}


async def read_input(request: Request, model: Type[BaseModel]) -> Tuple[BaseModel, List[bytes]]:
    """Parse a multipart (file fields + form options) or JSON (base64) body. Options may
    also be given as query parameters (e.g. ?avoid_eyes=true); the body wins."""
    query = {k: v for k, v in request.query_params.items() if k in model.model_fields}
    try:
        if request.headers.get("content-type", "").startswith("multipart/"):
            form = await request.form()
            files = [f for f in form.getlist("file") if hasattr(f, "read")]
            if not files:
                raise HTTPException(422, "multipart field 'file' required")
            opts = model(**{**query, **{k: v for k, v in form.items() if k != "file"}})
            return opts, [await f.read() for f in files]
        try:
            body = await request.json()
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"bad JSON: {e}")
        if not isinstance(body, dict):
            raise HTTPException(422, "JSON body must be an object")
        opts = model(**{**query, **body})
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False))
    data = getattr(opts, "image_base64", None)
    datas = [data] if data else (getattr(opts, "frames_base64", None) or [])
    if not datas:
        raise HTTPException(422, "image data required (image_base64 / frames_base64)")
    return opts, [b64(d) for d in datas]


# ---- app ------------------------------------------------------------------------------

def frames_of(datas: List[bytes], max_frames: int) -> list:
    frames = []
    for d in datas:
        frames += load_frames(d)
    if len(frames) > max_frames:
        step = len(frames) / max_frames
        frames = [frames[int(i * step)] for i in range(max_frames)]
    return frames


def create_app(mask: Optional[Mask] = None, connect_on_start: bool = False,
               stream_config: Optional[StreamConfig] = None) -> FastAPI:
    mask = mask or Mask()
    stream_config = stream_config or StreamConfig()

    @asynccontextmanager
    async def lifespan(app):
        if connect_on_start:
            try:
                await mask.connect()
            except Exception as e:  # keep serving; first request retries
                mask.last_error = repr(e)
        yield
        await mask.disconnect()

    app = FastAPI(title="Shining Mask API", version="0.3.0", lifespan=lifespan,
                  description="Control a Megoo / Shining Mask LED mask over BLE. "
                              "Full-panel DIY images are 46x58 RGB in slots 1-20.")
    app.state.mask = mask

    @app.exception_handler(TransportError)
    @app.exception_handler(AckTimeout)
    async def link_error(_, e):
        return JSONResponse(status_code=503, content={"detail": f"{type(e).__name__}: {e}"})

    @app.exception_handler(MaskError)
    async def mask_error(_, e):
        return JSONResponse(status_code=502, content={"detail": f"{type(e).__name__}: {e}"})

    @app.exception_handler(ShowError)
    async def show_error(_, e):
        return JSONResponse(status_code=409 if "overlap" in str(e) else 404 if "unknown show" in str(e) else 400,
                            content={"detail": str(e)})

    @app.exception_handler(UnidentifiedImageError)
    @app.exception_handler(ValueError)
    async def bad_value(_, e):
        return JSONResponse(status_code=400, content={"detail": str(e)})

    # connection

    @app.get("/status", tags=["connection"])
    async def status():
        return mask.status()

    @app.post("/connect", tags=["connection"], response_model=Ok)
    async def connect():
        await mask.connect()
        return Ok()

    @app.post("/disconnect", tags=["connection"], response_model=Ok)
    async def disconnect():
        await mask.disconnect()
        return Ok()

    # full-panel DIY

    @app.post("/diy", tags=["diy"], response_model=DiyResult, openapi_extra=body_spec(DiyOpts, "image_base64"),
              summary="Image -> 46x58 DIY slot (multipart file or JSON base64), then show it")
    async def diy(request: Request):
        opts, (data,) = await read_input(request, DiyOpts)
        panel = encode_panel(data, opts.fit, opts.avoid_eyes, opts.margin, opts.valign, mask.panel_map)
        if not opts.dry_run:
            await mask.upload_diy(panel, opts.slot, play=opts.play)
        return DiyResult(slots=[opts.slot], bytes=len(panel.payload), played=opts.play and not opts.dry_run,
                         uploaded=not opts.dry_run, preview_png_base64=[png_b64(panel.image)])

    @app.post("/animation", tags=["diy"], response_model=DiyResult,
              openapi_extra=body_spec(AnimationOpts, "frames_base64", multi=True),
              summary="Frames or an animated GIF -> consecutive DIY slots, then PLAY them all")
    async def animation(request: Request):
        opts, datas = await read_input(request, AnimationOpts)
        frames = frames_of(datas, opts.max_frames)
        panels = [encode_panel(f, opts.fit, opts.avoid_eyes, opts.margin, opts.valign, mask.panel_map)
                  for f in frames]
        slots = list(range(opts.start_slot, opts.start_slot + len(panels)))
        P.check_slot(slots[-1])
        if not opts.dry_run:
            await mask.upload_animation(panels, opts.start_slot, play=opts.play)
        return DiyResult(slots=slots, bytes=sum(len(p.payload) for p in panels),
                         played=opts.play and not opts.dry_run, uploaded=not opts.dry_run,
                         preview_png_base64=[png_b64(p.image) for p in panels])

    @app.post("/diy/play", tags=["diy"], response_model=Ok, summary="PLAY 1-20 slots (several = ~1 frame/s loop)")
    async def diy_play(req: SlotsReq):
        await mask.play_slots(req.slots)
        return Ok()

    @app.post("/diy/{slot}/play", tags=["diy"], response_model=Ok, summary="PLAY one slot")
    async def diy_play_one(slot: int = Path(ge=1, le=20)):
        await mask.play_slots([slot])
        return Ok()

    @app.delete("/diy", tags=["diy"], response_model=Ok, summary="DELE slots")
    async def diy_delete(req: SlotsReq):
        await mask.delete_slots(req.slots)
        return Ok()

    @app.get("/diy/count", tags=["diy"], response_model=CountResult, summary="CHEC: stored DIY image count")
    async def diy_count():
        return CountResult(count=await mask.diy_count())

    # shows

    @app.get("/shows", tags=["shows"], summary="Shows registry: order, current, name -> slots")
    async def shows():
        return mask.list_shows()

    @app.put("/shows/order", tags=["shows"], summary="Set the cycle order used by /next and the gesture")
    async def shows_order(req: OrderReq):
        mask.shows.set_order(req.order)
        return mask.list_shows()

    @app.post("/shows/{name}", tags=["shows"], response_model=ShowResult,
              openapi_extra=body_spec(ShowOpts, "frames_base64", multi=True),
              summary="Upload frames or a GIF (<=20) to consecutive slots and record them as a show")
    async def define_show(request: Request, name: str = Path(pattern=SHOW_NAME)):
        opts, datas = await read_input(request, ShowOpts)
        frames = frames_of(datas, opts.max_frames)
        panels = [encode_panel(f, opts.fit, opts.avoid_eyes, opts.margin, opts.valign, mask.panel_map)
                  for f in frames]
        previews = [png_b64(p.image) for p in panels]
        if opts.dry_run:
            slots = mask.shows.plan(name, len(panels), opts.start_slot, opts.force)
            return ShowResult(name=name, slots=slots, uploaded=False, preview_png_base64=previews)
        r = await mask.define_show(name, panels, opts.start_slot, play=opts.play, force=opts.force)
        return ShowResult(**r, uploaded=True, preview_png_base64=previews)

    @app.post("/shows/{name}/play", tags=["shows"], response_model=PlayShowResult, summary="PLAY a show's slots")
    async def play_show(name: str):
        return PlayShowResult(show=name, slots=await mask.play_show(name))

    @app.post("/next", tags=["shows"], response_model=PlayShowResult, summary="Play the next show in order")
    async def next_show():
        name = await mask.next_show()
        return PlayShowResult(show=name, slots=mask.shows.slots(name))

    @app.post("/gesture/our-shows", tags=["shows"], response_model=GestureShowsResult,
              summary="FACE: hand wave cycles the first slot of each show (stills)")
    async def gesture_shows(req: Optional[GestureShowsReq] = None):
        return GestureShowsResult(ids=await mask.gesture_our_shows((req and req.loop) == "random"))

    @app.get("/panel", tags=["diy"], summary="Panel geometry: oval, eye-hole cells, blocked map")
    async def panel(margin: int = Query(1, ge=0, le=5)):
        return (mask.panel_map or default_panel()).to_json(margin)

    # streaming

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        """JSON text frames {"type": slot|show|rhythm|image|band|stats, ...}; a binary frame
        is a full-panel PNG. Replies ack/error/stats. Query params override StreamConfig."""
        try:
            cfg = StreamConfig(**{**stream_config.__dict__, **{
                k: (int if k == "scratch_slot" else float)(v)
                for k, v in websocket.query_params.items() if k in stream_config.__dict__}})
        except (TypeError, ValueError) as e:
            await websocket.close(code=1008, reason=str(e)[:120])
            return
        await websocket.accept()
        out: asyncio.Queue = asyncio.Queue()
        streamer = Streamer(mask, cfg, on_event=out.put_nowait)

        async def sender():
            while True:
                await websocket.send_json(await out.get())

        send_task = asyncio.ensure_future(sender())
        streamer.start()
        out.put_nowait({"type": "hello", "config": cfg.__dict__})
        try:
            while True:
                m = await websocket.receive()
                if m["type"] == "websocket.disconnect":
                    break
                if m.get("bytes") is not None:
                    reply = await streamer.handle(m["bytes"])
                else:
                    try:
                        msg = json.loads(m.get("text") or "")
                    except json.JSONDecodeError as e:
                        reply = {"type": "error", "error": f"bad JSON: {e}"}
                    else:
                        reply = await streamer.handle(msg)
                out.put_nowait(reply)
        except WebSocketDisconnect:
            pass
        finally:
            await streamer.stop()
            send_task.cancel()

    # 16-row text / bitmap

    async def send_frame(frame, mode, brightness, speed, dry_run) -> FrameResult:
        if not dry_run:
            await mask.upload_frame(frame, mode, brightness, speed)
        return FrameResult(width=frame.width, bytes=len(frame.payload), mode=mode,
                           preview=frame.preview, uploaded=not dry_run)

    @app.post("/image", tags=["text"], response_model=FrameResult, openapi_extra=body_spec(ImageOpts, "image_base64"),
              summary="16-row, one-colour-per-column image (multipart file or JSON base64)")
    async def image(request: Request):
        opts, (data,) = await read_input(request, ImageOpts)
        frame = encode_image(data, opts.width or None, opts.threshold)
        return await send_frame(frame, opts.mode, opts.brightness, None, opts.dry_run)

    @app.post("/text", tags=["text"], response_model=FrameResult, summary="Render text to the 16-row path")
    async def text(req: TextReq):
        frame = encode_pixels(render_text(req.text, req.color, font_size=req.font_size, rainbow=req.rainbow), 0)
        mode = req.mode if req.mode is not None else (
            P.MODE_SCROLL_LEFT if frame.width > MASK_WIDTH else P.MODE_STEADY)
        result = await send_frame(frame, mode, req.brightness, req.speed, req.dry_run)
        if not req.dry_run:
            mask.state["text"] = req.text
        return result

    # built-ins and settings

    @app.post("/builtin/image/{idx}", tags=["builtin"], response_model=Ok, summary="IMAG idx (0-based)")
    async def builtin_image(idx: int = Path(ge=0, le=255), req: Optional[ExtraReq] = None):
        await mask.show_builtin_image(idx, *(req.extra if req else []))
        return Ok()

    @app.post("/builtin/animation/{idx}", tags=["builtin"], response_model=Ok, summary="ANIM idx (0-based)")
    async def builtin_animation(idx: int = Path(ge=0, le=255), req: Optional[ExtraReq] = None):
        await mask.show_builtin_animation(idx, *(req.extra if req else []))
        return Ok()

    @app.post("/builtin/loop", tags=["builtin"], response_model=Ok, summary="LOOA: loop all built-in animations")
    async def builtin_loop():
        await mask.loop_builtin_animations()
        return Ok()

    @app.post("/gesture", tags=["settings"], response_model=Ok, summary="FACE: hand-wave sensor cycling")
    async def gesture(req: GestureReq):
        select = {"all": P.SELECT_ALL, "builtin": P.SELECT_BUILTIN, "diy": P.SELECT_DIY}[req.select]
        await mask.set_gesture(req.enable, req.loop == "random", select, req.ids)
        return Ok()

    @app.post("/brightness", tags=["settings"], response_model=Ok)
    async def brightness(req: Level):
        await mask.set_brightness(req.level)
        return Ok()

    @app.post("/mode", tags=["settings"], response_model=Ok)
    async def mode(req: ModeReq):
        await mask.set_mode(req.mode)
        return Ok()

    @app.post("/speed", tags=["settings"], response_model=Ok)
    async def speed(req: SpeedReq):
        await mask.set_speed(req.speed)
        return Ok()

    @app.post("/color/foreground", tags=["settings"], response_model=Ok, summary="FC en R G B")
    async def fg(req: ColorReq):
        await mask.set_foreground(req.color, req.enable)
        return Ok()

    @app.post("/color/background", tags=["settings"], response_model=Ok, summary="BC en R G B")
    async def bg(req: ColorReq):
        await mask.set_background(req.color, req.enable)
        return Ok()

    @app.post("/text-mode", tags=["settings"], response_model=Ok, summary="M flag mode")
    async def text_mode(req: TextModeReq):
        await mask.set_text_mode(req.enable, req.mode)
        return Ok()

    @app.post("/rhythm", tags=["settings"], response_model=Ok,
              summary="One mic/rhythm frame (24 bars 0-9); stream every ~120 ms")
    async def rhythm(req: RhythmReq):
        await mask.send_rhythm(req.mode, req.bars)
        return Ok()

    # raw

    @app.post("/raw", tags=["raw"], response_model=RawResult,
              summary="Send one command by name+args or as hex plaintext")
    async def raw(req: RawReq):
        plain = P.pad(bytes.fromhex(req.hex)) if req.hex is not None else P.plaintext(req.name.upper(), *req.args)
        char = P.MUSIC_CHAR if req.char == "music" else P.CMD_CHAR
        notes = await mask.send_raw(plain, expect=req.expect, wait=req.wait, char=char)
        return RawResult(plaintext=plain.hex(), notifications=notes)

    @app.post("/upload", tags=["raw"], response_model=Ok,
              summary="Generic DATS/chunks/DATCP upload of an arbitrary payload")
    async def upload(req: UploadReq):
        payload = req.payload()
        if req.dats_hex:
            dats = P.pad(bytes.fromhex(req.dats_hex))
        elif req.kind == "diy":
            dats = P.dats_diy(len(payload), req.slot)
        else:
            dats = P.dats_text(len(payload), req.bitmap_len, req.flag)
        if req.datcp_hex:
            datcp = P.pad(bytes.fromhex(req.datcp_hex))
        else:
            datcp = P.datcp_diy() if req.kind == "diy" else P.datcp()
        after = [(str(c[0]).upper(), *(int(a) for a in c[1:])) for c in req.after if c]
        await mask.upload_generic(payload, dats, datcp, after)
        return Ok()

    return app


def main(argv=None):
    import uvicorn

    from .fake import FakeTransport

    ap = argparse.ArgumentParser(prog="maskapi serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--address", help="mask MAC; default scans for a MASK* device")
    ap.add_argument("--connect", action="store_true", help="connect at startup instead of on first request")
    ap.add_argument("--idle-disconnect", type=float, default=0, help="drop the link after N idle seconds (0 = never)")
    ap.add_argument("--fake", action="store_true", help="use an in-memory fake mask (no Bluetooth)")
    ap.add_argument("--shows", help="shows registry JSON (default $MASKAPI_SHOWS or ~/.maskapi/shows.json)")
    ap.add_argument("--eyes", help="eyes.json for avoid_eyes / GET /panel")
    d = StreamConfig()
    ap.add_argument("--scratch-slot", type=int, default=d.scratch_slot, help="/ws image slot")
    ap.add_argument("--rhythm-interval", type=float, default=d.rhythm_interval, help="/ws min s between rhythm writes")
    ap.add_argument("--play-interval", type=float, default=d.play_interval, help="/ws min s between PLAY switches")
    ap.add_argument("--image-interval", type=float, default=d.image_interval, help="/ws min s between image uploads")
    ap.add_argument("--band-interval", type=float, default=d.band_interval, help="/ws min s between band uploads")
    ap.add_argument("--stats-interval", type=float, default=d.stats_interval, help="/ws stats period (0 = off)")
    ap.add_argument("--log-level", default="info")
    a = ap.parse_args(argv)
    logging.basicConfig(level=a.log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .panel import PanelMap

    mask = Mask(FakeTransport() if a.fake else None, address=a.address, idle_disconnect=a.idle_disconnect,
                shows=a.shows, panel_map=PanelMap.load(a.eyes) if a.eyes else None)
    cfg = StreamConfig(a.scratch_slot, a.rhythm_interval, a.play_interval, a.image_interval, a.band_interval,
                       a.stats_interval)
    uvicorn.run(create_app(mask, a.connect, cfg), host=a.host, port=a.port, log_level=a.log_level)


if __name__ == "__main__":
    main()
