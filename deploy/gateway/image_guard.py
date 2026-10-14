"""Send no model an image it cannot open.

A model API refuses a whole request when any image in it does not decode,
and Claude Code resends the whole history on every turn. One broken picture
in a conversation therefore fails every later request the same way, retries
included: an agent that wrote a PNG with its own encoder and read it back put
an undecodable image into its history, and each of the following turns ended
in a 400 until the conversation was abandoned. Every provider behind this
gateway takes the same four formats and refuses what it cannot decode, so
the check here is one rule for all of them rather than a reading of any
provider's error.

Before a request goes upstream, every inline image in it — in a user
message, in a tool result, in any of the three request shapes the gateway
serves — is opened with Pillow and decoded in full. One that is not valid
base64, is not PNG, JPEG, GIF or WebP by its bytes, does not decode, or is
larger than 8000 px on a side is replaced by a text block saying so. The
text depends on the image's bytes alone, so every later request carries the
same text and the prompt cache still holds. Images that open are passed on
untouched: nothing is resized or re-encoded. An image given by URL is not
fetched, and passes as it came.

Installed into site-packages as `cheese_image_guard` and registered in
config.yaml under `litellm_settings.callbacks`.
"""

import asyncio
import base64
import binascii
import hashlib
import io
import logging
from collections import OrderedDict

from litellm.integrations.custom_logger import CustomLogger
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger("cheese.image_guard")
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(logging.StreamHandler())

#: The four raster formats every provider behind the gateway takes, by
#: Pillow's name for each.
FORMATS = frozenset({"PNG", "JPEG", "GIF", "WEBP"})
#: The longest side every one of them takes (Anthropic's limit is the
#: lowest). Read from the header before anything is decoded, which also keeps
#: a small file that unpacks to an enormous picture from being decoded here.
MAX_SIDE = 8000
#: Verdicts kept, by the image's SHA-256. A conversation resends its images on
#: every turn; each is decoded once.
CACHE_SIZE = 4096

_verdicts: "OrderedDict[str, str | None]" = OrderedDict()


def problem(raw: bytes) -> str | None:
    """Why no model would open these bytes, or None when they open."""
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in FORMATS:
                return "not PNG, JPEG, GIF or WebP"
            if max(image.size) > MAX_SIDE:
                return f"larger than {MAX_SIDE} px on a side"
            image.load()
    except UnidentifiedImageError:
        return "not PNG, JPEG, GIF or WebP"
    except Image.DecompressionBombError:
        return f"larger than {MAX_SIDE} px on a side"
    except Exception:
        return "could not be decoded"
    return None


async def verdict(data: str) -> tuple[str, str | None]:
    """The image's SHA-256 (of its base64 text) and why it does not open, or
    None when it does."""
    digest = hashlib.sha256(data.encode()).hexdigest()
    if digest in _verdicts:
        _verdicts.move_to_end(digest)
        return digest, _verdicts[digest]
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        reason = "not valid base64"
    else:
        # Decoding a large picture takes long enough to hold up every other
        # request on the event loop.
        reason = await asyncio.to_thread(problem, raw)
    _verdicts[digest] = reason
    if len(_verdicts) > CACHE_SIZE:
        _verdicts.popitem(last=False)
    return digest, reason


def placeholder(reason: str, digest: str) -> str:
    return f"[image omitted by the gateway: {reason}; sha256 {digest[:12]}]"


def inline_data(block: dict) -> tuple[str, str] | None:
    """(the base64 text, the type of the text block that replaces it) of an
    inline image block in any of the gateway's request shapes, or None for
    anything else, an image given by URL included."""
    kind = block.get("type")
    if kind == "image":
        # Messages API.
        source = block.get("source")
        if isinstance(source, dict) and source.get("type") == "base64":
            data = source.get("data")
            return (data, "text") if isinstance(data, str) else None
        return None
    if kind == "image_url":
        # Chat Completions: {"image_url": {"url": ...}} or {"image_url": "..."}.
        url = block.get("image_url")
        url = url.get("url") if isinstance(url, dict) else url
        kind_of_text = "text"
    elif kind == "input_image":
        # Responses API.
        url = block.get("image_url")
        kind_of_text = "input_text"
    else:
        return None
    if not isinstance(url, str) or not url.startswith("data:"):
        return None
    header, _, data = url.partition(",")
    if not header.endswith(";base64"):
        return None
    return data, kind_of_text


async def checked(value: object, model: object) -> object:
    """`value` with every inline image in it that does not open replaced by
    a text block; the same object when nothing was replaced."""
    if isinstance(value, list):
        items = [await checked(item, model) for item in value]
        if all(new is old for new, old in zip(items, value)):
            return value
        return items
    if not isinstance(value, dict):
        return value
    found = inline_data(value)
    if found is not None:
        data, text_type = found
        digest, reason = await verdict(data)
        if reason is None:
            return value
        logger.info(
            "image_guard replaced image: reason=%r sha256=%s model_group=%s",
            reason,
            digest[:12],
            model,
        )
        replacement = {"type": text_type, "text": placeholder(reason, digest)}
        if "cache_control" in value:
            # Keep a cache breakpoint where the caller put it.
            replacement["cache_control"] = value["cache_control"]
        return replacement
    changed = {}
    for key, item in value.items():
        if isinstance(item, (list, dict)):
            new = await checked(item, model)
            if new is not item:
                changed[key] = new
    return {**value, **changed} if changed else value


class ImageGuard(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        for field in ("messages", "input"):
            if isinstance(data.get(field), list):
                data[field] = await checked(data[field], data.get("model"))
        return data


handler = ImageGuard()
