"""Immutable raw transcript increments in private object storage."""

import asyncio
import hashlib
import io
import json
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.storage import S3StorageBackend, StorageBackend
from app.domain.topic.models import RawTranscript

CHUNK_BYTES = 1024 * 1024
# One object transfer, end to end. Every object here is at most CHUNK_BYTES, and
# the SDK's own timeouts bound a single socket read, not the whole operation:
# a transfer that never gets a connection waits forever without this.
TRANSFER_SECONDS = 60


def source_record(
    project_id: uuid.UUID, topic_id: uuid.UUID, file_id: uuid.UUID, source: str
) -> tuple[str, bytes]:
    # This independent identity record allows rebuilding the DB index from the
    # immutable offset/hash object keys after restoring an older DB backup.
    key = f"transcripts/raw/{project_id}/{topic_id}/{file_id}/source.json"
    content = json.dumps(
        {
            "project_id": str(project_id),
            "topic_id": str(topic_id),
            "id": str(file_id),
            "source": source,
        },
        sort_keys=True,
    ).encode()
    return key, content


def transcript_storage() -> StorageBackend:
    # Transcript keys are private and are served through room authorization.
    # Do not fall back to the public uploads directory when S3 is unconfigured.
    if (
        not settings.s3_endpoint_url
        or not settings.s3_access_key
        or not settings.s3_secret_key
        or not settings.transcript_s3_bucket
    ):
        raise RuntimeError("Private transcript object storage is not configured")
    return S3StorageBackend(
        bucket=settings.transcript_s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )


def validate_source(source: str) -> str:
    path = PurePosixPath(source)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in source
        or "\x00" in source
        or len(source) > 1024
        or source != str(path)
        or len(path.parts) < 3
        or path.parts[:2] != (".claude", "projects")
        or path.suffix != ".jsonl"
    ):
        raise ValidationError("Invalid transcript source path")
    return source


async def _upload(backend: StorageBackend, content: bytes, key: str, kind: str):
    async with asyncio.timeout(TRANSFER_SECONDS):
        await backend.upload(io.BytesIO(content), key, kind)


async def _download(backend: StorageBackend, key: str) -> bytes | None:
    async with asyncio.timeout(TRANSFER_SECONDS):
        return await backend.download(key)


def _insert_missing(
    project_id: uuid.UUID, topic_id: uuid.UUID, file_id: uuid.UUID, source: str
):
    now = datetime.now(UTC)
    return (
        insert(RawTranscript)
        .values(
            id=file_id,
            project_id=project_id,
            topic_id=topic_id,
            source=source,
            size=0,
            chunks=[],
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[RawTranscript.id])
    )


def _stored_receipt(row: RawTranscript | None, receipt: dict) -> dict | None:
    """The receipt already on file for this range, or None if it is new.

    Raises where the range cannot be appended: the row belongs to another
    file, the same offset holds different bytes, or the range leaves a gap.
    """
    if row is None:
        if receipt["offset"] != 0:
            raise ConflictError("Transcript upload is not contiguous")
        return None
    for chunk in row.chunks:
        if chunk["offset"] == receipt["offset"]:
            if any(chunk[k] != v for k, v in receipt.items()):
                raise ConflictError("Transcript range already contains different bytes")
            return receipt
    if receipt["offset"] != row.size:
        raise ConflictError("Transcript upload is not contiguous")
    return None


async def _row(
    session: AsyncSession,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    file_id: uuid.UUID,
    source: str,
    *,
    lock: bool = False,
) -> RawTranscript | None:
    statement = select(RawTranscript).where(RawTranscript.id == file_id)
    if lock:
        # The session keeps objects across commit, so the locked read must
        # reload the row rather than hand back what the unlocked read saw.
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    row = await session.scalar(statement)
    if row is not None and (row.project_id, row.topic_id, row.source) != (
        project_id,
        topic_id,
        source,
    ):
        raise ConflictError("Transcript identity belongs to another file")
    return row


async def append(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    file_id: uuid.UUID,
    source: str,
    offset: int,
    content: bytes,
    storage: StorageBackend | None = None,
) -> dict:
    """Acknowledge only after object storage and the database commit succeed.

    The object goes to storage before the row is locked: a transfer that
    stalls then stalls only its own request, while a lock held across it
    would queue every later upload of this file behind it, each holding a
    pool connection, until the pool is gone and the whole app is down with it
    (dev, 2026-09-18). Keys carry offset and digest, so a repeat upload of
    the same bytes is the same object.
    """
    validate_source(source)
    if offset < 0 or not content or len(content) > CHUNK_BYTES:
        raise ValidationError("Invalid transcript byte range")
    digest = hashlib.sha256(content).hexdigest()
    receipt = {"offset": offset, "size": len(content), "sha256": digest}
    # Read without a lock first, so a retry or a bad range is answered before
    # any transfer; the locked re-check below is what actually decides.
    stored = _stored_receipt(
        await _row(session, project_id, topic_id, file_id, source), receipt
    )
    await session.commit()
    if stored is not None:
        return stored
    key = f"transcripts/raw/{project_id}/{topic_id}/{file_id}/{offset:020d}-{digest}"
    backend = storage or transcript_storage()
    if offset == 0:
        identity_key, identity = source_record(project_id, topic_id, file_id, source)
        await _upload(backend, identity, identity_key, "application/json")
    await _upload(backend, content, key, "application/octet-stream")
    await session.execute(_insert_missing(project_id, topic_id, file_id, source))
    row = await _row(session, project_id, topic_id, file_id, source, lock=True)
    assert row is not None
    stored = _stored_receipt(row, receipt)
    if stored is None:
        row.chunks = [*row.chunks, {**receipt, "key": key}]
        row.size += len(content)
    await session.commit()
    return receipt


async def files(
    session: AsyncSession, project_id: uuid.UUID, topic_id: uuid.UUID
) -> list[dict]:
    rows = await session.scalars(
        select(RawTranscript)
        .where(
            RawTranscript.project_id == project_id, RawTranscript.topic_id == topic_id
        )
        .order_by(RawTranscript.created_at, RawTranscript.id)
    )
    return [{"id": str(row.id), "source": row.source, "size": row.size} for row in rows]


async def confirm(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    file_id: uuid.UUID,
    source: str,
    size: int,
    sha256: str,
    offset: int = 0,
    storage: StorageBackend | None = None,
) -> dict:
    validate_source(source)
    if offset < 0 or size < 0 or size > CHUNK_BYTES:
        raise ValidationError("Invalid transcript confirmation range")
    backend = storage or transcript_storage()
    identity_key, identity = source_record(project_id, topic_id, file_id, source)
    if offset == 0 and size == 0 and sha256 == hashlib.sha256().hexdigest():
        # An empty file has no chunk upload to create its row, so this does;
        # the identity object is written before the row is locked, as in append.
        try:
            row = await _row(session, project_id, topic_id, file_id, source)
        except ConflictError as exc:
            raise NotFoundError("Transcript not found") from exc
        await session.commit()
        if row is None or row.size == 0:
            await _upload(backend, identity, identity_key, "application/json")
        await session.execute(_insert_missing(project_id, topic_id, file_id, source))
        try:
            row = await _row(session, project_id, topic_id, file_id, source, lock=True)
        except ConflictError as exc:
            raise NotFoundError("Transcript not found") from exc
        assert row is not None
        if row.size != 0:
            raise ConflictError("Transcript is not empty")
        await session.commit()
    row = await session.get(RawTranscript, file_id)
    if row is None or (row.project_id, row.topic_id, row.source) != (
        project_id,
        topic_id,
        source,
    ):
        raise NotFoundError("Transcript not found")
    if await _download(backend, identity_key) != identity:
        raise RuntimeError("Transcript identity record is unavailable or corrupt")
    chunks = [chunk for chunk in row.chunks if chunk["offset"] == offset]
    if size and (
        len(chunks) != 1 or chunks[0]["size"] != size or chunks[0]["sha256"] != sha256
    ):
        raise ConflictError("Transcript range does not match stored bytes")
    await session.commit()
    digest = hashlib.sha256()
    stored_size = 0
    async for content in contents(chunks, storage=storage):
        stored_size += len(content)
        digest.update(content)
    if stored_size != size or digest.hexdigest() != sha256:
        raise ConflictError("Transcript is not fully stored")
    return {
        "id": str(file_id),
        "source": source,
        "offset": offset,
        "size": size,
        "sha256": sha256,
    }


async def read(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    topic_id: uuid.UUID,
    file_id: uuid.UUID,
    storage: StorageBackend | None = None,
) -> bytes:
    row = await session.get(RawTranscript, file_id)
    if row is None or (row.project_id, row.topic_id) != (project_id, topic_id):
        raise NotFoundError("Transcript not found")
    chunks = list(row.chunks)
    await session.commit()
    result = bytearray()
    async for content in contents(chunks, storage=storage):
        result.extend(content)
    return bytes(result)


async def contents(chunks: list[dict], *, storage: StorageBackend | None = None):
    """Download at most one bounded object at a time, including final verification."""
    if not chunks:
        return
    backend = storage or transcript_storage()
    for chunk in chunks:
        content = await _download(backend, chunk["key"])
        if (
            content is None
            or len(content) != chunk["size"]
            or hashlib.sha256(content).hexdigest() != chunk["sha256"]
        ):
            raise RuntimeError("Transcript object is unavailable or corrupt")
        yield content
