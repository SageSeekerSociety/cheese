"""The raster image types the platform shows as pictures, and how to tell one
from its first bytes.

PNG, JPEG, GIF and WebP are the four every browser and every model takes as an
image. SVG is not one of them on purpose: it is markup that can carry script,
so it stays a file — downloaded, never rendered inline, never sent to a model
as an image.

Standard library only and valid on Python 3.9: the executor ships this file to
every machine (`runtime.RELEASE_FILES`) for `machine_files.py`, which must not
import anything from the backend.
"""

from __future__ import annotations

#: Media type of each raster image suffix, lower case with its dot.
SUFFIX_MEDIA_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

#: The suffix a file of each raster media type is saved under.
MEDIA_TYPE_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def is_raster(media_type: str | None) -> bool:
    return media_type in MEDIA_TYPE_SUFFIX


def sniff(head: bytes) -> str | None:
    """The raster media type `head` (a file's first bytes, 12 are enough)
    starts with, or None for anything else."""
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    for magic, media_type in _MAGIC:
        if head.startswith(magic):
            return media_type
    return None
