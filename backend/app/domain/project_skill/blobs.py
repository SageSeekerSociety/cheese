"""A project skill's files, kept in the platform's file storage by content.

A skill and each of its revisions hold a manifest, ``{path: {"sha256", "size"}}``;
the bytes live once under ``project-skills/<sha256>``, whichever skill, project
or revision names them. Ten revisions of a skill that changed its body ten
times hold its files once, and listing skills reads no file at all.

A blob is written before the row that names it is committed. Writing is
idempotent, so a rolled-back save leaves at most an unreferenced blob, never a
manifest that points at nothing. Nothing removes blobs yet.
"""

from __future__ import annotations

import hashlib
import io

from app.core.storage import StorageBackend

PREFIX = "project-skills"


def _key(sha256: str) -> str:
    return f"{PREFIX}/{sha256}"


async def put(storage: StorageBackend, data: bytes) -> dict:
    sha256 = hashlib.sha256(data).hexdigest()
    if not await storage.exists(_key(sha256)):
        await storage.upload(io.BytesIO(data), _key(sha256), "application/octet-stream")
    return {"sha256": sha256, "size": len(data)}


async def store(storage: StorageBackend, files: dict[str, str]) -> dict[str, dict]:
    """Text files → the manifest that names them, their bytes stored."""
    return {path: await put(storage, text.encode()) for path, text in files.items()}


async def read(storage: StorageBackend, manifest: dict[str, dict]) -> dict[str, str]:
    """The manifest's files as text. A missing blob is a broken store, not a
    file to leave out quietly."""
    out: dict[str, str] = {}
    for path, entry in manifest.items():
        data = await storage.download(_key(entry["sha256"]))
        if data is None:
            raise FileNotFoundError(f"project skill file {path} ({entry['sha256']})")
        out[path] = data.decode("utf-8")
    return out
