"""Reading and writing documents and their node trees."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    ColumnElement,
    Uuid,
    and_,
    column,
    delete,
    or_,
    select,
    table,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rank import Rank
from app.core.request_facts import recall
from app.domain.living_doc.models import Document, DocumentNode

# Which documents a task or a project points at. Bare tables: ``room_task`` and
# ``project`` depend on this domain, and importing back would make a cycle.
_tasks = table("tasks", column("document_id", Uuid))
_projects = table("projects", column("overview_document_id", Uuid))


def project_own(project_id: uuid.UUID) -> ColumnElement[bool]:
    """The project's own documents: not a task's living document, not the
    project's goals, and not one that still belongs to the conversation 芝士
    wrote it in. The library lists and searches these."""
    return and_(
        Document.project_id == project_id,
        or_(Document.room_id.is_(None), Document.kept_at.is_not(None)),
        Document.id.not_in(
            select(_tasks.c.document_id).where(_tasks.c.document_id.is_not(None))
        ),
        Document.id.not_in(
            select(_projects.c.overview_document_id).where(
                _projects.c.overview_document_id.is_not(None)
            )
        ),
    )


#: The projects table, read here only for the team a project belongs to: the
#: project package sits above this one and imports it.
PROJECTS = table("projects", column("id"), column("team_id"))


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def of_project(
        self,
        project_id: uuid.UUID,
        *,
        after: Rank | None,
        limit: int,
        also_of_room: uuid.UUID | None = None,
    ) -> list[Document]:
        """The project's own documents, no task's, the latest changed first —
        the order `app.core.rank` writes down, picking up after ``after``.
        ``also_of_room`` adds the ones that channel or private chat keeps."""
        own = project_own(project_id)
        if also_of_room is not None:
            own = or_(
                own,
                and_(
                    Document.project_id == project_id,
                    Document.room_id == also_of_room,
                ),
            )
        query = select(Document).where(own)
        if after is not None:
            query = query.where(
                or_(
                    Document.updated_at < after.at,
                    and_(
                        Document.updated_at == after.at,
                        Document.id > uuid.UUID(after.tie),
                    ),
                )
            )
        rows = await self._session.scalars(
            query.order_by(Document.updated_at.desc(), Document.id).limit(limit)
        )
        return list(rows)

    async def of_team(self, team_id: int) -> list[Document]:
        """The team's own documents — none of its projects' — the latest
        changed first. A person's own documents are their personal team's."""
        rows = await self._session.scalars(
            select(Document)
            .where(Document.team_id == team_id, Document.project_id.is_(None))
            .order_by(Document.updated_at.desc(), Document.id)
        )
        return list(rows)

    async def of_room(self, room_id: uuid.UUID) -> list[Document]:
        """The documents 芝士 wrote in this channel or private chat, saved to
        the library or not, the newest first."""
        rows = await self._session.scalars(
            select(Document)
            .where(Document.room_id == room_id)
            .order_by(Document.created_at.desc(), Document.id)
        )
        return list(rows)

    async def create(
        self,
        *,
        project_id: uuid.UUID | None,
        team_id: int | None = None,
        title: str | None = None,
        author: str = "system",
        created_by: str | None = None,
        drafted_by: str | None = None,
        number: int | None = None,
        room_id: uuid.UUID | None = None,
    ) -> Document:
        """A new document, empty (version 0): the project's own, one a task
        points at (which goes by the task's title), one written in the
        conversation ``room_id`` and kept there, or — with no project — one of
        the team ``team_id``'s own. A project's document is its team's too."""
        if project_id is not None and team_id is None:
            if isinstance(self._session, AsyncSession):
                found_proj, cached_proj = recall(
                    self._session, ("project_by_id", project_id)
                )
                if found_proj and cached_proj is not None:
                    team_id = cached_proj.team_id
            if team_id is None:
                team_id = await self._session.scalar(
                    select(PROJECTS.c.team_id).where(PROJECTS.c.id == project_id)
                )
        if team_id is None:
            # i18n-exempt: an invariant, never shown to a person
            raise ValueError("A document belongs to a team")
        doc = Document(
            team_id=team_id,
            project_id=project_id,
            title=title,
            author=author,
            created_by=created_by,
            drafted_by=drafted_by,
            number=number,
            room_id=room_id,
        )
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def keep(self, doc: Document) -> None:
        """Save it to the library: from now on it is the project's."""
        if doc.kept_at is None:
            doc.kept_at = datetime.now(UTC)
            await self._session.flush()

    async def set_content(
        self, doc: Document, content: str, *, author: str | None, expected_version: int
    ) -> Document | None:
        """Make ``content`` the next version, but only while the document is
        still at ``expected_version``. Returns the document, or None when
        somebody else wrote it first. ``author`` None keeps the last one.

        The check is the WHERE clause, not an `if` above the write: two writers
        that read the same version would both pass a comparison made here.
        """
        now = datetime.now(UTC)
        values: dict = {
            "content": content,
            "version": Document.version + 1,
            "updated_at": now,
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
        doc.content = content
        doc.version = expected_version + 1
        doc.updated_at = now
        if author is not None:
            doc.author = author
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
