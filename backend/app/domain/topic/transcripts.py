"""Where the platform keeps a place's raw Claude session files.

A place that ran on a device leaves its claude home there, and in it the
session files (`.claude/projects/**/*.jsonl`) and the todo lists beside them.
The room's conversation is in the `blocks` table; these are the agent's own
transcripts, and nothing else holds them. So before a home is deleted
(topic/retire.py) the device ships those two directories here as one tar.gz,
and this module is the receiving end: it streams the body onto disk under
`settings.transcripts_dir`, refuses one over `settings.transcripts_max_bytes`,
checks that what arrived is a whole archive, and only then gives it its name —
`<project>/<place>/<utc timestamp>.tar.gz`, a new file per upload, never an
overwrite. A half-written archive is a `.part` file that is removed on any
failure, so nothing under the final name is ever incomplete.

Nothing here extracts. Member names are still checked for `..` and absolute
paths so that whoever reads an archive later can extract it without a second
look.
"""

import asyncio
import gzip
import hashlib
import os
import tarfile
import tempfile
import uuid
import zlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings


class ArchiveTooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"archive exceeds {limit} bytes")
        self.limit = limit


class NotAnArchive(Exception):
    """The body is not a complete, safe tar.gz."""


@dataclass(frozen=True)
class StoredArchive:
    path: Path
    size: int
    sha256: str


def place_dir(project_id: uuid.UUID, place_id: uuid.UUID) -> Path:
    return Path(settings.transcripts_dir) / str(project_id) / str(place_id)


async def store(
    project_id: uuid.UUID,
    place_id: uuid.UUID,
    body: AsyncIterator[bytes],
    *,
    max_bytes: int | None = None,
) -> StoredArchive:
    """Write one uploaded archive for the place and return where it landed.

    Raises ``ArchiveTooLarge`` past the cap (nothing is kept) and
    ``NotAnArchive`` when the bytes do not open as a tar.gz or a member name
    escapes the archive."""
    limit = settings.transcripts_max_bytes if max_bytes is None else max_bytes
    target_dir = place_dir(project_id, place_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    # The temp file lives in the target directory so the final rename is one
    # atomic operation on the same filesystem.
    fd, tmp_name = tempfile.mkstemp(prefix=".upload-", suffix=".part", dir=target_dir)
    tmp = Path(tmp_name)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            async for chunk in body:
                size += len(chunk)
                if size > limit:
                    raise ArchiveTooLarge(limit)
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise NotAnArchive("empty body")
        # A full pass over the archive, so off the event loop.
        await asyncio.to_thread(_check_archive, tmp)
        final = _fresh_name(target_dir)
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return StoredArchive(path=final, size=size, sha256=digest.hexdigest())


def _check_archive(path: Path) -> None:
    try:
        with (
            gzip.open(path, "rb") as stream,
            tarfile.open(fileobj=stream, mode="r|") as archive,
        ):
            for member in archive:
                parts = Path(member.name).parts
                if member.name.startswith("/") or ".." in parts:
                    raise NotAnArchive(f"member escapes the archive: {member.name}")
            # tar stops at its own end-of-archive marker, and the gzip trailer
            # (CRC and length) sits after that: it is only checked when the
            # stream is read through, which is what turns a cut-off upload
            # into an error instead of an archive that merely looks whole.
            while stream.read(1 << 20):
                pass
    except (tarfile.TarError, EOFError, OSError, zlib.error) as exc:
        raise NotAnArchive(f"not a complete tar.gz: {exc}") from exc


def _fresh_name(target_dir: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    candidate = target_dir / f"{stamp}.tar.gz"
    n = 0
    # Two uploads in the same microsecond is not a case worth designing for,
    # but overwriting one is not an option either.
    while candidate.exists():
        n += 1
        candidate = target_dir / f"{stamp}-{n}.tar.gz"
    return candidate
