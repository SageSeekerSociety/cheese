"""Links between a project's documents (#3827): a ``<#id>`` in one document's
text that names another document of the same project.

What a document links to is read from its stored text, so it is kept on every
store (``sync``) rather than asked of the editor. What links *to* a document is
the reverse of those rows, plus the conversation messages whose references name
it (`block.citations`).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.living_doc.models import Document, DocumentLink

#: A ``<#id>`` reference, as a message or a document writes it.
_REF = re.compile(r"<#([0-9a-fA-F-]{36})>")


def referenced(text: str) -> list[uuid.UUID]:
    """The ids ``text`` references with ``<#id>``, once each, in order."""
    found: list[uuid.UUID] = []
    for raw in _REF.findall(text or ""):
        try:
            value = uuid.UUID(raw)
        except ValueError:
            continue
        if value not in found:
            found.append(value)
    return found


async def documents_among(
    db: AsyncSession, project_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> list[uuid.UUID]:
    """Which of ``ids`` are documents of this project."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    found = set(
        await db.scalars(
            select(Document.id).where(
                Document.id.in_(wanted), Document.project_id == project_id
            )
        )
    )
    return [i for i in wanted if i in found]


async def sync(db: AsyncSession, doc: Document, content: str) -> None:
    """Keep what ``doc`` links to as its stored ``content`` says."""
    targets = [
        t
        for t in await documents_among(db, doc.project_id, referenced(content))
        if t != doc.id
    ]
    await db.execute(delete(DocumentLink).where(DocumentLink.source_id == doc.id))
    for target in targets:
        db.add(DocumentLink(source_id=doc.id, target_id=target))
    await db.flush()


async def linking_to(db: AsyncSession, document_id: uuid.UUID) -> list[Document]:
    """The documents whose text links to this one, most recently changed first."""
    return list(
        await db.scalars(
            select(Document)
            .join(DocumentLink, DocumentLink.source_id == Document.id)
            .where(DocumentLink.target_id == document_id)
            .order_by(Document.updated_at.desc())
        )
    )


async def links_of(db: AsyncSession, document_id: uuid.UUID) -> list[Document]:
    """The documents this one's text links to, as it links them."""
    return list(
        await db.scalars(
            select(Document)
            .join(DocumentLink, DocumentLink.target_id == Document.id)
            .where(DocumentLink.source_id == document_id)
        )
    )
