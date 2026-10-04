"""Reading and writing documents and their node trees."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.models import Document, DocumentNode


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def of_room(self, room_id: uuid.UUID) -> Document | None:
        """The room's living document, if it has one yet."""
        return await self._session.scalar(
            select(Document).where(Document.room_id == room_id)
        )

    async def of_rooms(self, room_ids: list[uuid.UUID]) -> dict[uuid.UUID, Document]:
        """Several rooms' living documents at once, keyed by room id."""
        if not room_ids:
            return {}
        rows = await self._session.scalars(
            select(Document).where(Document.room_id.in_(room_ids))
        )
        return {row.room_id: row for row in rows if row.room_id is not None}

    async def ensure_for_room(
        self, *, room_id: uuid.UUID, project_id: uuid.UUID
    ) -> Document:
        """The room's living document, created empty (version 0) the first
        time anyone needs to address it."""
        await self._session.execute(
            insert(Document)
            .values(id=uuid.uuid4(), project_id=project_id, room_id=room_id)
            .on_conflict_do_nothing(index_elements=[Document.room_id])
        )
        doc = await self.of_room(room_id)
        assert doc is not None
        return doc

    async def create(self, *, project_id: uuid.UUID) -> Document:
        """A new empty document (version 0) of the project's, in no room."""
        doc = Document(id=uuid.uuid4(), project_id=project_id)
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def set_content(
        self, doc: Document, content: str, *, author: str | None, expected_version: int
    ) -> Document | None:
        """Make ``content`` the next version, but only while the document is
        still at ``expected_version``. Returns the document, or None when
        somebody else wrote it first. ``author`` None keeps the last one.

        The check is the WHERE clause, not an `if` above the write: two writers
        that read the same version would both pass a comparison made here.
        """
        values: dict = {
            "content": content,
            "version": Document.version + 1,
            "updated_at": datetime.now(UTC),
        }
        if author is not None:
            values["author"] = author
        written = await self._session.scalar(
            update(Document)
            .where(Document.id == doc.id, Document.version == expected_version)
            .values(**values)
            .returning(Document.id)
        )
        if written is None:
            return None
        await self._session.refresh(doc)
        return doc

    async def nodes(self, document_id: uuid.UUID) -> list[DocumentNode]:
        """The document's top-level blocks, in order."""
        return list(
            await self._session.scalars(
                select(DocumentNode)
                .where(DocumentNode.document_id == document_id)
                .order_by(DocumentNode.position)
            )
        )

    def add_node(
        self,
        *,
        document_id: uuid.UUID,
        node_type: str,
        content: str,
        position: float,
        author: str,
    ) -> DocumentNode:
        node = DocumentNode(
            document_id=document_id,
            node_type=node_type,
            content=content,
            position=position,
            author=author,
        )
        self._session.add(node)
        return node

    async def delete_nodes(self, ids: list[uuid.UUID]) -> None:
        if ids:
            await self._session.execute(
                delete(DocumentNode).where(DocumentNode.id.in_(ids))
            )
