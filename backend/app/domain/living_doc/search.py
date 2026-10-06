"""Finding words in documents, for the project search
(`GET /projects/{id}/context/search`).

Three kinds of hit, as the search names them: ``doc`` (a whole document),
``doc_node`` (one of its top-level blocks, which is the paragraph a hit can
point at) and ``comment`` (a comment written on it). All are ranked by the same
BM25 query the search runs on messages (`app.domain.search.bm25`).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Uuid, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.models import Document, DocumentComment, DocumentNode
from app.domain.living_doc.repositories import project_own
from app.domain.search import bm25

KINDS = ("doc", "doc_node", "comment")
_MODELS = {"doc": Document, "doc_node": DocumentNode, "comment": DocumentComment}


@dataclass
class Hit:
    kind: str
    id: uuid.UUID
    document: Document
    author: str
    created_at: datetime
    content: str
    score: float


# Which documents tasks and projects point at. Bare tables: ``room_task`` and
# ``project`` depend on this domain.
_tasks = table(
    "tasks", column("id", Uuid), column("room_id", Uuid), column("document_id", Uuid)
)
_projects = table(
    "projects", column("root_topic_id", Uuid), column("overview_document_id", Uuid)
)


async def homes(
    session: AsyncSession, room_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID | None]]:
    """Where each living document of these channels is read, by document id:
    ``(channel, task)``. A task's document is under its task; the project's
    overview under 综合."""
    if not room_ids:
        return {}
    out: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID | None]] = {}
    tasks = await session.execute(
        select(_tasks.c.document_id, _tasks.c.room_id, _tasks.c.id).where(
            _tasks.c.room_id.in_(room_ids), _tasks.c.document_id.is_not(None)
        )
    )
    for document_id, room_id, task_id in tasks:
        out[document_id] = (room_id, task_id)
    overviews = await session.execute(
        select(_projects.c.overview_document_id, _projects.c.root_topic_id).where(
            _projects.c.root_topic_id.in_(room_ids),
            _projects.c.overview_document_id.is_not(None),
        )
    )
    for document_id, room_id in overviews:
        out[document_id] = (room_id, None)
    return out


async def readable(
    session: AsyncSession, room_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Document]:
    """The written living documents of these channels, by id (see `homes`)."""
    where = await homes(session, room_ids)
    if not where:
        return {}
    rows = await session.scalars(
        select(Document).where(Document.id.in_(list(where)), Document.version > 0)
    )
    return {row.id: row for row in rows}


async def own(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[uuid.UUID, Document]:
    """The project's own documents, written or not, by id."""
    rows = await session.scalars(select(Document).where(project_own(project_id)))
    return {row.id: row for row in rows}


def _matching(kind: str, terms: list[str], ids: list[uuid.UUID]) -> ColumnElement[bool]:
    if kind == "doc":
        return bm25.match_all_words(
            Document.id, terms, {"content": 1}, filters=[bm25.any_of("id", ids)]
        )
    return bm25.match_all_words(
        _MODELS[kind].id,
        terms,
        {"content": 1},
        filters=[bm25.any_of("document_id", ids)],
    )


async def find(
    session: AsyncSession,
    kind: str,
    terms: list[str],
    docs: dict[uuid.UUID, Document],
    *,
    limit: int,
    offset: int = 0,
) -> list[Hit]:
    """The best ``limit`` hits of ``kind`` after ``offset``, best first."""
    if not docs:
        return []
    model = _MODELS[kind]
    score = func.paradedb.score(model.id)
    rows = await session.execute(
        select(model, score)
        .where(_matching(kind, terms, list(docs)))
        .order_by(score.desc(), model.id)
        .offset(offset)
        .limit(limit)
    )
    hits = []
    for row, value in rows:
        doc = row if kind == "doc" else docs[row.document_id]
        hits.append(
            Hit(
                kind=kind,
                id=row.id,
                document=doc,
                author=row.author,
                created_at=row.updated_at if kind == "doc" else row.created_at,
                content=row.content,
                score=float(value or 0),
            )
        )
    return hits


async def count(
    session: AsyncSession, kind: str, terms: list[str], docs: dict[uuid.UUID, Document]
) -> int:
    if not docs:
        return 0
    model = _MODELS[kind]
    return (
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(_matching(kind, terms, list(docs)))
        )
        or 0
    )
