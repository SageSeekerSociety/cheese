"""Store task bundles in private object storage; the forge remains authoritative."""

import asyncio
import hashlib
import re
import uuid
from typing import Any, BinaryIO, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.core.storage import S3StorageBackend, StorageBackend
from app.domain.room_task.models import Task, TaskSnapshot


def private_storage() -> StorageBackend:
    # Bundles hold working files, so they go to the private bucket only; with no
    # private bucket configured this refuses rather than use the public one.
    if (
        not settings.s3_endpoint_url
        or not settings.s3_access_key
        or not settings.s3_secret_key
        or not settings.transcript_s3_bucket
    ):
        raise RuntimeError("Private object storage is not configured")
    return S3StorageBackend(
        bucket=settings.transcript_s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )


async def save(
    session: AsyncSession,
    task: Task,
    *,
    file: BinaryIO,
    head_sha: str,
    snapshot_sha: str,
    digest: str,
    storage: StorageBackend | None = None,
) -> TaskSnapshot:
    if not all(
        re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)
        for value in (head_sha, snapshot_sha)
    ):
        raise ValidationError(say("snapshotCommitInvalid"))
    file.seek(0)
    # BinaryIO and SpooledTemporaryFile both supply the readinto interface.
    actual_digest = await asyncio.to_thread(
        hashlib.file_digest, cast(Any, file), "sha256"
    )
    if actual_digest.hexdigest() != digest:
        raise ValidationError(say("snapshotTransferCorrupt"))
    file.seek(0)
    if file.readline() not in (b"# v2 git bundle\n", b"# v3 git bundle\n"):
        raise ValidationError(say("snapshotNotBundle"))
    file.seek(0)
    existing = await session.scalar(
        select(TaskSnapshot)
        .where(
            TaskSnapshot.task_id == task.id,
            TaskSnapshot.digest == digest,
        )
        .limit(1)
    )
    if existing is not None:
        if (await latest(session, task.id)).id == existing.id:
            return existing
        # The checkout went back to files it was already backed up with. Those
        # bytes are stored; what has to change is which backup is latest, or a
        # recovery restores the edit that was undone.
        key = existing.storage_key
    else:
        key = (
            f"task-snapshots/{task.project_id}/{task.id}/{snapshot_sha}/{digest}.bundle"
        )
        # This bucket is private. Public upload URLs must never carry working
        # files.
        storage = storage or private_storage()
        async with asyncio.timeout(120):
            await storage.upload(file, key, "application/x-git-bundle")
    row = TaskSnapshot(
        task_id=task.id,
        head_sha=head_sha,
        snapshot_sha=snapshot_sha,
        digest=digest,
        storage_key=key,
    )
    session.add(row)
    await session.flush()
    return row


async def latest(session: AsyncSession, task_id: uuid.UUID) -> TaskSnapshot:
    row = await session.scalar(
        select(TaskSnapshot)
        .where(TaskSnapshot.task_id == task_id)
        .order_by(TaskSnapshot.created_at.desc())
        .limit(1)
    )
    if row is None:
        raise NotFoundError(say("snapshotNone"))
    return row


async def download(
    row: TaskSnapshot, *, storage: StorageBackend | None = None
) -> bytes:
    storage = storage or private_storage()
    async with asyncio.timeout(120):
        content = await storage.download(row.storage_key)
    if content is None:
        raise NotFoundError(say("snapshotFileMissing"))
    if hashlib.sha256(content).hexdigest() != row.digest:
        raise ValidationError(say("snapshotCheckFailed"))
    return content
