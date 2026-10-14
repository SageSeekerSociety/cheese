"""Image bytes for tests: real pictures of each format, and a PNG that does not
decode."""

import io
import struct
import zlib

from PIL import Image


def picture(format: str) -> bytes:
    """A small real image in Pillow's ``format`` (PNG, JPEG, GIF, WEBP, TIFF)."""
    out = io.BytesIO()
    Image.new("RGB", (4, 3), (200, 30, 30)).save(out, format=format)
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
