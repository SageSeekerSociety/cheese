"""Data access for the document tree (`document` table). No business logic, no authz."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.document.models import DocType, Document


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        project_id: int,
        parent_id: int | None,
        title: str,
        created_by: int,
        doc_root_block_id: int | None,
        sort_order: float = 0.0,
        doc_type: DocType = DocType.MARKDOWN,
    ) -> Document:
        now = datetime.now(UTC)
        doc = Document(
            project_id=project_id,
            parent_id=parent_id,
            title=title,
            doc_type=doc_type,
            doc_root_block_id=doc_root_block_id,
            sort_order=sort_order,
            created_by=created_by,
            archived=False,
            created_at=now,
            updated_at=now,
        )
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def get(self, document_id: int) -> Document | None:
        return await self._session.get(Document, document_id)

    async def list_for_project(self, project_id: int) -> list[Document]:
        """All documents of a project, ordered so the API can assemble the tree."""
        rows = (
            await self._session.execute(
                select(Document)
                .where(Document.project_id == project_id)
                .order_by(Document.sort_order, Document.id)
            )
        ).scalars()
        return list(rows)

    async def children_of(self, project_id: int, parent_id: int | None) -> list[Document]:
        rows = (
            await self._session.execute(
                select(Document)
                .where(Document.project_id == project_id, Document.parent_id == parent_id)
                .order_by(Document.sort_order, Document.id)
            )
        ).scalars()
        return list(rows)

    async def next_sort_order(self, project_id: int, parent_id: int | None) -> float:
        siblings = await self.children_of(project_id, parent_id)
        return max((s.sort_order for s in siblings), default=-1.0) + 1.0

    async def update(
        self,
        doc: Document,
        *,
        title: str | None = None,
        parent_id: int | None = None,
        set_parent: bool = False,
        sort_order: float | None = None,
        archived: bool | None = None,
    ) -> Document:
        if title is not None:
            doc.title = title
        if set_parent:
            doc.parent_id = parent_id
        if sort_order is not None:
            doc.sort_order = sort_order
        if archived is not None:
            doc.archived = archived
        doc.updated_at = datetime.now(UTC)
        await self._session.flush()
        return doc

    async def touch(self, doc: Document) -> Document:
        doc.updated_at = datetime.now(UTC)
        await self._session.flush()
        return doc

    async def delete(self, doc: Document) -> None:
        await self._session.delete(doc)
        await self._session.flush()
