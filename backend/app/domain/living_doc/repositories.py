"""Reading and writing documents and their node trees."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, Uuid, and_, column, delete, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.models import Document, DocumentNode

# Which documents a task or a project points at. Bare tables: ``room_task`` and
# ``project`` depend on this domain, and importing back would make a cycle.
_tasks = table("tasks", column("document_id", Uuid))
_projects = table("projects", column("overview_document_id", Uuid))


def project_own(project_id: uuid.UUID) -> ColumnElement[bool]:
    """The project's own documents: not a task's living document and not the
    project's overview. The library lists and searches these."""
    return and_(
        Document.project_id == project_id,
        Document.id.not_in(
            select(_tasks.c.document_id).where(_tasks.c.document_id.is_not(None))
        ),
        Document.id.not_in(
            select(_projects.c.overview_document_id).where(
                _projects.c.overview_document_id.is_not(None)
            )
        ),
    )


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def of_project(self, project_id: uuid.UUID) -> list[Document]:
        """The project's own documents, no task's, the latest changed first."""
        rows = await self._session.scalars(
            select(Document)
            .where(project_own(project_id))
            .order_by(Document.updated_at.desc(), Document.id)
        )
        return list(rows)

    async def create(
        self, *, project_id: uuid.UUID, title: str | None = None, author: str = "system"
    ) -> Document:
        """A new document in no room, empty (version 0): the project's own, or
        one a task points at (which goes by the task's title)."""
        doc = Document(project_id=project_id, title=title, author=author)
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
