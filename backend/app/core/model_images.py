"""Whether bytes are a picture a model will take, decided by decoding them.

A file's name, the media type a browser sent with it, and its first bytes all
say what a file claims to be; none of them says it decodes. A model's API
decodes every image it is sent and refuses the whole request when one does not,
and the picture stays in the conversation, so every later request is refused
the same way and the conversation is over. So nothing goes to a model as a
picture unless it decoded here first.

Pillow, so this module stays on the backend: ``raster_images`` is the
standard-library half that ships to every machine.
"""

from __future__ import annotations

import io
from typing import BinaryIO

from PIL import Image

#: The formats every provider takes as an image, by Pillow's name for each.
_MEDIA_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}

#: The longest side every provider takes; Anthropic's 8000 px is the smallest.
#: Read from the header before anything is decoded, so it also caps what a
#: decompression bomb can make this process allocate: 64 million pixels.
MAX_SIDE = 8000

#: The most bytes one picture may have. The Claude API takes an image of up to
#: 10 MB once base64-encoded, which is 7.5 MB of the file itself; a larger one
#: refuses the request, and the conversation with it. It also bounds what a turn
#: reads into memory for a picture: a file of several GB sent as one is never
#: read (`app.domain.agent.prompt.model_images`).
MAX_BYTES = 10_000_000 * 3 // 4


def model_media_type(source: bytes | BinaryIO) -> str | None:
    """The media type ``source`` decodes as, or None when a model would refuse it.

    A file object is read from its start and left at its start."""
    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    stream.seek(0)
    try:
        with Image.open(stream) as image:
            media_type = _MEDIA_TYPES.get(image.format or "")
            if media_type is None or max(image.size) > MAX_SIDE:
                return None
            # The whole image, not `verify()`: verify reads the chunk structure
            # and passes a PNG whose compressed pixels are garbage.
            image.load()
    # Malformed input raises whatever the failing decoder raises — OSError,
    # SyntaxError, ValueError, zlib.error, struct.error, EOFError — and every one
    # of them means the same thing here.
    except Exception:
        return None
    finally:
        stream.seek(0)
    return media_type
