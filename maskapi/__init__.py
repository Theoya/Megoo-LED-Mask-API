"""Library + HTTP API for the Megoo "Shining Mask" BLE LED mask.

    from maskapi import Mask
    async with Mask() as m:
        await m.upload_diy("face.png", slot=1, avoid_eyes=True)
        await m.next_show()

See docs/API.md.
"""
from .client import AckTimeout, Mask, MaskError
from .image import (Frame, Panel, collect_frames, encode_image, encode_panel, encode_pixels, load_frames,
                    render_text)
from .panel import PanelMap, default_panel, place
from .shows import ShowError, ShowRegistry
from .stream import StreamConfig, Streamer
from .transport import BleakTransport, Transport, TransportError

__all__ = ["Mask", "MaskError", "AckTimeout", "Transport", "BleakTransport", "TransportError",
           "Frame", "Panel", "encode_image", "encode_panel", "encode_pixels", "load_frames", "render_text",
           "collect_frames", "PanelMap", "default_panel", "place", "ShowRegistry", "ShowError",
           "Streamer", "StreamConfig"]
