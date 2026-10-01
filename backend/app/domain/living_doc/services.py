"""Journal primitives. The caller owns commit, including document/node/event effects.

No block or topic repositories are imported here. Ordinary writes acquire the
same durable room lock before reading their base; operation claims and replay
are serialized by that lock too, including the first creation of a document.
"""

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.domain.living_doc.models import (
    DocumentLock,
    DocumentOperation,
    DocumentVersion,
)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def payload_fingerprint(payload: dict) -> str:
    return content_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True))


class DocumentJournal:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock(self, room_id: uuid.UUID) -> None:
        await self.session.execute(
            insert(DocumentLock)
            .values(room_id=room_id)
            .on_conflict_do_nothing(index_elements=[DocumentLock.room_id])
        )
        await self.session.execute(
            select(DocumentLock)
            .where(DocumentLock.room_id == room_id)
            .with_for_update()
        )

    async def claim(
        self,
        *,
        room_id: uuid.UUID,
        actor: str,
        action: str,
        operation_id: uuid.UUID,
        payload: dict,
    ) -> DocumentOperation:
        await self.lock(room_id)
        fingerprint = payload_fingerprint(payload)
        row = await self.session.scalar(
            select(DocumentOperation).where(
                DocumentOperation.room_id == room_id,
                DocumentOperation.actor == actor,
                DocumentOperation.action == action,
                DocumentOperation.operation_id == operation_id,
            )
        )
        if row is not None:
            if row.fingerprint != fingerprint:
                raise ConflictError("同一 operation_id 已用于不同的文档请求")
            return row
        row = DocumentOperation(
            room_id=room_id,
            actor=actor,
            action=action,
            operation_id=operation_id,
            fingerprint=fingerprint,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def append(
        self,
        *,
        room_id: uuid.UUID,
        document_id: uuid.UUID,
        version: int,
        content: str,
        actor: str,
        base_version: int | None,
        operation_id: uuid.UUID | None = None,
        event_id: uuid.UUID | None = None,
    ) -> None:
        self.session.add(
            DocumentVersion(
                room_id=room_id,
                document_id=document_id,
                version=version,
                content=content,
                content_hash=content_hash(content),
                previous_version=version - 1 if version > 1 else None,
                base_version=base_version,
                actor=actor,
                operation_id=operation_id,
                event_id=event_id,
            )
        )
        await self.session.flush()

    async def seed_existing(
        self,
        *,
        room_id: uuid.UUID,
        document_id: uuid.UUID,
        version: int,
        content: str,
        actor: str,
    ) -> None:
        """Preserve the deployed snapshot; do not invent pre-journal history."""
        existing = await self.session.scalar(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version == version,
            )
        )
        if existing is None:
            await self.append(
                room_id=room_id,
                document_id=document_id,
                version=version,
                content=content,
                actor=actor,
                base_version=None,
            )

    async def finish(self, operation: DocumentOperation, receipt: dict) -> None:
        operation.receipt = receipt
        await self.session.flush()

    async def receipt(
        self, *, room_id: uuid.UUID, actor: str, action: str, operation_id: uuid.UUID
    ) -> dict | None:
        row = await self.session.scalar(
            select(DocumentOperation).where(
                DocumentOperation.room_id == room_id,
                DocumentOperation.actor == actor,
                DocumentOperation.action == action,
                DocumentOperation.operation_id == operation_id,
            )
        )
        return row.receipt if row else None

    async def history(self, room_id: uuid.UUID, *, after: int = 0) -> list[dict]:
        rows = await self.session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.room_id == room_id, DocumentVersion.version > after)
            .order_by(DocumentVersion.version)
            .limit(100)
        )
        return [
            {
                "document_id": str(row.document_id),
                "version": row.version,
                "content": row.content,
                "content_hash": row.content_hash,
                "previous_version": row.previous_version,
                "base_version": row.base_version,
                "actor": row.actor,
                "operation_id": str(row.operation_id) if row.operation_id else None,
                "event_id": str(row.event_id) if row.event_id else None,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
