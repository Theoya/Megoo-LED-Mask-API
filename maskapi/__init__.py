"""Library + HTTP API for the Megoo "Shining Mask" BLE LED mask.

    from maskapi import Mask
    async with Mask() as m:
        await m.upload_diy("face.png", slot=1)

See docs/API.md.
"""
from .client import AckTimeout, Mask, MaskError
from .image import Frame, Panel, encode_image, encode_panel, encode_pixels, load_frames, render_text
from .transport import BleakTransport, Transport, TransportError

__all__ = ["Mask", "MaskError", "AckTimeout", "Transport", "BleakTransport", "TransportError",
           "Frame", "Panel", "encode_image", "encode_panel", "encode_pixels", "load_frames", "render_text"]
