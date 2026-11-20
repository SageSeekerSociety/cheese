"""Links between a team's documents (#3827): a ``<#id>`` in one document's
text that names another document of the same team — of one of its projects, or
of the team's own (#3774).

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
from app.domain.living_doc.repositories import PROJECTS

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
    db: AsyncSession, team_id: int, ids: Iterable[uuid.UUID]
) -> list[uuid.UUID]:
    """Which of ``ids`` are documents of this team, its projects' included."""
    wanted = list(dict.fromkeys(ids))
    if not wanted:
        return []
    found = set(
        await db.scalars(
            select(Document.id).where(
                Document.id.in_(wanted), Document.team_id == team_id
            )
        )
    )
    return [i for i in wanted if i in found]


async def documents_of_projects_team(
    db: AsyncSession, project_id: uuid.UUID, ids: Iterable[uuid.UUID]
) -> list[uuid.UUID]:
    """Which of ``ids`` are documents a text of this project may link to: its
    team's, the team's own and its projects'."""
    team_id = await db.scalar(
        select(PROJECTS.c.team_id).where(PROJECTS.c.id == project_id)
    )
    return await documents_among(db, team_id, ids) if team_id is not None else []


async def sync(
    db: AsyncSession, doc: Document, content: str, *, before: str | None = None
) -> None:
    """Keep what ``doc`` links to as its stored ``content`` says."""
    refs = referenced(content)
    if not refs and before is not None and not referenced(before):
        return
    targets = [t for t in await documents_among(db, doc.team_id, refs) if t != doc.id]
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
