"""Journal primitives. The caller owns commit, including document/node/event effects.

No block or topic repositories are imported here. Ordinary writes acquire the
same durable room lock before reading their base; operation claims and replay
are serialized by that lock too, including the first creation of a document.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.sentences import say
from app.domain.living_doc.models import (
    DocumentLock,
    DocumentOperation,
    DocumentState,
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
                raise ConflictError(say("docOperationIdReused"))
            if row.receipt is None:
                raise ConflictError(say("docOperationNoReceipt"))
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

    async def replay(
        self,
        *,
        room_id: uuid.UUID,
        actor: str,
        action: str,
        operation_id: uuid.UUID,
        payload: dict,
    ) -> dict | None:
        """A completed operation's receipt, read without the room lock.

        For the write paths that go through the collaboration service: they
        must not hold the lock across that call (its store takes it), so they
        look for a finished receipt first and leave the claim to the store.
        """
        row = await self.session.scalar(
            select(DocumentOperation).where(
                DocumentOperation.room_id == room_id,
                DocumentOperation.actor == actor,
                DocumentOperation.action == action,
                DocumentOperation.operation_id == operation_id,
            )
        )
        if row is None:
            return None
        if row.fingerprint != payload_fingerprint(payload):
            raise ConflictError(say("docOperationIdReused"))
        return row.receipt

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
        requested_by: str | None = None,
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
                requested_by=requested_by,
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

    async def version_content(self, room_id: uuid.UUID, version: int) -> str | None:
        return await self.session.scalar(
            select(DocumentVersion.content).where(
                DocumentVersion.room_id == room_id,
                DocumentVersion.version == version,
            )
        )

    async def history(self, room_id: uuid.UUID, *, after: int = 0) -> list[dict]:
        rows = await self.session.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.room_id == room_id, DocumentVersion.version > after)
            .order_by(DocumentVersion.version)
            .limit(100)
        )
        return [_version_row(row) for row in rows]

    async def recent(
        self, room_id: uuid.UUID, *, before: int | None = None, limit: int = 30
    ) -> list[dict]:
        """The newest versions first, those below ``before`` when it is given."""
        query = select(DocumentVersion).where(DocumentVersion.room_id == room_id)
        if before is not None:
            query = query.where(DocumentVersion.version < before)
        rows = await self.session.scalars(
            query.order_by(DocumentVersion.version.desc()).limit(limit)
        )
        return [_version_row(row) for row in rows]

    async def state(self, room_id: uuid.UUID) -> bytes | None:
        return await self.session.scalar(
            select(DocumentState.state).where(DocumentState.room_id == room_id)
        )

    async def put_state(
        self,
        room_id: uuid.UUID,
        state: bytes,
        suggestions: list[dict] | None = None,
        reasons: dict[str, str] | None = None,
    ) -> None:
        """Keep the live document's state and the suggestions pending in it.

        ``reasons`` are the reasons given for suggestions this store proposes;
        a suggestion proposed earlier keeps the reason it was stored with.
        """
        kept = {
            item.get("id"): item.get("reason")
            for item in await self.suggestions(room_id)
            if item.get("reason")
        }
        pending = [
            {
                "id": str(item["id"]),
                "author": str(item.get("author") or ""),
                "old": str(item.get("old") or ""),
                "new": str(item.get("new") or ""),
                "reason": (reasons or {}).get(item["id"]) or kept.get(item["id"]),
            }
            for item in suggestions or []
        ]
        await self.session.execute(
            insert(DocumentState)
            .values(
                room_id=room_id,
                state=state,
                suggestions=pending,
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=[DocumentState.room_id],
                set_={
                    "state": state,
                    "suggestions": pending,
                    "updated_at": datetime.now(UTC),
                },
            )
        )

    async def suggestions(self, room_id: uuid.UUID) -> list[dict]:
        """The suggestions pending in the live document at its last store."""
        found = await self.session.scalar(
            select(DocumentState.suggestions).where(DocumentState.room_id == room_id)
        )
        return list(found or [])


def _version_row(row: DocumentVersion) -> dict:
    return {
        "document_id": str(row.document_id),
        "version": row.version,
        "content": row.content,
        "content_hash": row.content_hash,
        "previous_version": row.previous_version,
        "base_version": row.base_version,
        "actor": row.actor,
        "requested_by": row.requested_by,
        "operation_id": str(row.operation_id) if row.operation_id else None,
        "event_id": str(row.event_id) if row.event_id else None,
        "created_at": row.created_at.isoformat(),
    }
