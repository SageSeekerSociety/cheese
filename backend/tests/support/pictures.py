"""Image bytes for tests: real pictures of each format, and a PNG that does not
decode."""

import base64
import io
import struct
import zlib

from PIL import Image

#: Display P3 — what a phone writes into a photo saved in that gamut. This one
#: is Google's "Display P3 Gamut with sRGB Transfer". A picture carrying it makes
#: the browser render the whole page in P3, and a phone that switches its
#: display colour mode for that shifts every pixel on the screen.
DISPLAY_P3_PROFILE = base64.b64decode(
    "AAACCAAAAAAEMAAAbW50clJHQiBYWVogB+AAAQABAAAAAAAAYWNzcAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAEAAPbWAAEAAAAA0y0AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAJZGVzYwAAAPAAAABkclhZWgAAAVQAAAAUZ1hZWgAAAWgAAAAUYlhZ"
    "WgAAAXwAAAAUd3RwdAAAAZAAAAAUclRSQwAAAaQAAAAoZ1RSQwAAAaQAAAAoYlRSQwAAAaQAAAAo"
    "Y3BydAAAAcwAAAA8bWx1YwAAAAAAAAABAAAADGVuVVMAAABGAAAAHABEAGkAcwBwAGwAYQB5ACAA"
    "UAAzACAARwBhAG0AdQB0ACAAdwBpAHQAaAAgAHMAUgBHAEIAIABUAHIAYQBuAHMAZgBlAHIAAFhZ"
    "WiAAAAAAAACD3QAAPb7///+7WFlaIAAAAAAAAEq/AACxNwAACrlYWVogAAAAAAAAKDsAABELAADI"
    "y1hZWiAAAAAAAAD21gABAAAAANMtcGFyYQAAAAAABAAAAAJmZgAA8qcAAA1ZAAAT0AAAClsAAAAA"
    "AAAAAG1sdWMAAAAAAAAAAQAAAAxlblVTAAAAIAAAABwARwBvAG8AZwBsAGUAIABJAG4AYwAuACAA"
    "MgAwADEANg=="
)


def picture(format: str) -> bytes:
    """A small real image in Pillow's ``format`` (PNG, JPEG, GIF, WEBP, TIFF)."""
    out = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 30, 30)).save(out, format=format)
    return out.getvalue()


def wide_gamut_picture(format: str = "JPEG") -> bytes:
    """The same little picture, tagged Display P3 instead of sRGB."""
    out = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 30, 30)).save(
        out, format=format, icc_profile=DISPLAY_P3_PROFILE
    )
    return out.getvalue()


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body))
    )


#: What a hand-written encoder made when a session had no image library: a PNG
#: signature and a well-formed header, then pixel data that does not inflate.
#: Its first bytes say PNG to anything that only sniffs them.
BROKEN_PNG = (
    b"\x89PNG\r\n\x1a\n"
    + _chunk(b"IHDR", struct.pack(">IIBBBBB", 2000, 640, 8, 6, 0, 0, 0))
    + _chunk(b"IDAT", b"\x78\x9c" + b"\x00garbage, not deflate" * 8)
    + _chunk(b"IEND", b"")
)
