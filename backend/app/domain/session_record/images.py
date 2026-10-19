"""Upload journal sidecars before persisting the records that refer to them."""

import base64
import hashlib
import io
import uuid

from app.core.journal_images import MAX_IMAGE_BYTES
from app.core.storage import StorageBackend


def image_key(conversation_id: uuid.UUID, digest: str) -> str:
    return f"session-record-images/{conversation_id}/{digest}"


async def keep(
    rows: list[dict], conversation_id: uuid.UUID, storage: StorageBackend
) -> None:
    for row in rows:
        for digest, encoded in row.get("images", {}).items():
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > MAX_IMAGE_BYTES or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("journal image checksum or size mismatch")
            key = image_key(conversation_id, digest)
            if not await storage.exists(key):
                await storage.upload(io.BytesIO(raw), key, "application/octet-stream")
            if await storage.download(key) != raw:
                raise RuntimeError("session image archive verification failed")
