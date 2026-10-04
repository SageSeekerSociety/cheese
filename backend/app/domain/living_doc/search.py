"""Finding words in documents, for the project search
(`GET /projects/{id}/context/search`).

Two kinds of hit, as the search names them: ``doc`` (a whole document) and
``doc_node`` (one of its top-level blocks, which is the paragraph a hit can
point at). Both are ranked by the same BM25 query the search runs on
messages (`app.domain.search.bm25`).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.models import Document, DocumentNode
from app.domain.search import bm25

KINDS = ("doc", "doc_node")


@dataclass
class Hit:
    kind: str
    id: uuid.UUID
    document: Document
    author: str
    created_at: datetime
    content: str
    score: float


async def readable(
    session: AsyncSession, room_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Document]:
    """The written documents of these rooms, by id."""
    if not room_ids:
        return {}
    rows = await session.scalars(
        select(Document).where(Document.room_id.in_(room_ids), Document.version > 0)
    )
    return {row.id: row for row in rows}


def _matching(kind: str, terms: list[str], ids: list[uuid.UUID]) -> ColumnElement[bool]:
    if kind == "doc":
        return bm25.match_all_words(
            Document.id, terms, {"content": 1}, filters=[bm25.any_of("id", ids)]
        )
    return bm25.match_all_words(
        DocumentNode.id,
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
    model = Document if kind == "doc" else DocumentNode
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
    model = Document if kind == "doc" else DocumentNode
    return (
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(_matching(kind, terms, list(docs)))
        )
        or 0
    )
