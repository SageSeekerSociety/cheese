"""Immutable raw transcript increments in private object storage."""

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
    """Acknowledge only after object storage and the database commit succeed."""
    validate_source(source)
    if offset < 0 or not content or len(content) > CHUNK_BYTES:
        raise ValidationError("Invalid transcript byte range")
    now = datetime.now(UTC)
    await session.execute(
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
    row = (
        await session.scalars(
            select(RawTranscript).where(RawTranscript.id == file_id).with_for_update()
        )
    ).one()
    if (row.project_id, row.topic_id, row.source) != (project_id, topic_id, source):
        raise ConflictError("Transcript identity belongs to another file")
    digest = hashlib.sha256(content).hexdigest()
    receipt = {"offset": offset, "size": len(content), "sha256": digest}
    for chunk in row.chunks:
        if chunk["offset"] == offset:
            if any(chunk[k] != v for k, v in receipt.items()):
                raise ConflictError("Transcript range already contains different bytes")
            await session.commit()
            return receipt
    if offset != row.size:
        raise ConflictError("Transcript upload is not contiguous")
    key = f"transcripts/raw/{project_id}/{topic_id}/{file_id}/{offset:020d}-{digest}"
    backend = storage or transcript_storage()
    if offset == 0:
        identity_key, identity = source_record(project_id, topic_id, file_id, source)
        await backend.upload(io.BytesIO(identity), identity_key, "application/json")
    await backend.upload(io.BytesIO(content), key, "application/octet-stream")
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
        now = datetime.now(UTC)
        await session.execute(
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
        row = (
            await session.scalars(
                select(RawTranscript)
                .where(RawTranscript.id == file_id)
                .with_for_update()
            )
        ).one()
        if (row.project_id, row.topic_id, row.source) != (project_id, topic_id, source):
            raise NotFoundError("Transcript not found")
        if row.size != 0:
            raise ConflictError("Transcript is not empty")
        await backend.upload(io.BytesIO(identity), identity_key, "application/json")
        await session.commit()
    row = await session.get(RawTranscript, file_id)
    if row is None or (row.project_id, row.topic_id, row.source) != (
        project_id,
        topic_id,
        source,
    ):
        raise NotFoundError("Transcript not found")
    if await backend.download(identity_key) != identity:
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
        content = await backend.download(chunk["key"])
        if (
            content is None
            or len(content) != chunk["size"]
            or hashlib.sha256(content).hexdigest() != chunk["sha256"]
        ):
            raise RuntimeError("Transcript object is unavailable or corrupt")
        yield content
