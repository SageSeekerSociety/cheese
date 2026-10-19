"""Content-addressed journal images. Standard library only: ships with runners."""

import base64
import hashlib
from typing import Any

MAX_IMAGE_BYTES = 10 * 1024 * 1024


def extract(value, connection):
    """Replace inline image bytes by their digest, retaining at most 10 MB each."""
    root = [value]
    pending: list[tuple[Any, Any]] = [(root, 0)]
    while pending:
        holder, index = pending.pop()
        item = holder[index]
        if isinstance(item, dict):
            if item.get("type") == "base64" and str(
                item.get("media_type", "")
            ).startswith("image/"):
                holder[index] = _keep(
                    base64.b64decode(item["data"], validate=True),
                    item["media_type"],
                    connection,
                )
            else:
                copy = dict(item)
                holder[index] = copy
                pending.extend((copy, key) for key in copy)
        elif isinstance(item, list):
            copy = list(item)
            holder[index] = copy
            pending.extend((copy, n) for n in range(len(copy)))
    return root[0]


def _keep(raw: bytes, media_type: str, connection) -> dict:
    if len(raw) > MAX_IMAGE_BYTES:
        return {"type": "omitted", "bytes": len(raw), "media_type": media_type}
    digest = hashlib.sha256(raw).hexdigest()
    connection.execute("INSERT OR IGNORE INTO images VALUES (?, ?)", (digest, raw))
    return {
        "type": "session_image",
        "sha256": digest,
        "bytes": len(raw),
        "media_type": media_type,
    }


def attached(record: dict, connection) -> dict[str, str]:
    """Only this row's pixels travel with a journal page; the record stays small."""
    found = {}
    for digest in digests(record):
        row = connection.execute(
            "SELECT data FROM images WHERE sha256 = ?", (digest,)
        ).fetchone()
        if row is not None:
            found[digest] = base64.b64encode(row[0]).decode()
    return found


def digests(record):
    pending = [record]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            if value.get("type") == "session_image":
                yield value["sha256"]
            pending.extend(value.values())


def reference(record, connection, sequence):
    connection.executemany(
        "INSERT OR IGNORE INTO image_refs VALUES (?, ?)",
        ((sequence, digest) for digest in digests(record)),
    )


def prune(connection, table):
    """Release sidecar pixels only after every record referring to them is gone."""
    connection.execute(
        f"DELETE FROM image_refs WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE {table}.sequence = image_refs.sequence)"
    )
    connection.execute(
        "DELETE FROM images WHERE NOT EXISTS (SELECT 1 FROM image_refs WHERE image_refs.sha256 = images.sha256)"
    )


def import_attached(entries: list[dict], connection) -> None:
    for entry in entries:
        for digest, encoded in entry.get("images", {}).items():
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > MAX_IMAGE_BYTES or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("journal image checksum or size mismatch")
            connection.execute(
                "INSERT OR IGNORE INTO images VALUES (?, ?)", (digest, raw)
            )
