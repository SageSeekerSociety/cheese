"""项目的目标：它是哪一份文档，和它的第一版。

项目目标是**一份文档**（``documents`` 的一行），项目那一行只记着它的编号
（``projects.overview_document_id``）。「打开综合时先要的那一份」和「把平台已有的
文字写成它的第一版」两件事认的都是文档，所以它们住在这里，而不是住在项目那边
——`project` 在第 2 层，够不着第 4 层的本领域。

那一行的编号走裸表读写，理由和 ``repositories.project_own`` 里那张 ``_projects``
一样：``project`` 依赖本领域，导回来就成环（`.importlinter` 的 C3）。
"""

import uuid

from sqlalchemy import Uuid, column, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.request_facts import recall, remember
from app.core.sentences import say
from app.domain.living_doc.models import Document
from app.domain.living_doc.services import DocumentJournal, Documents

# The project row that names one of its documents as the project's goals. A bare
# table: ``project`` depends on this domain, and importing back would make a cycle.
_projects = table("projects", column("id", Uuid), column("overview_document_id", Uuid))


def _stale(current_version: int) -> ConflictError:
    """The document moved under a first-version writer. It is not theirs to
    overwrite; the version rides along so the caller can re-read and rebase."""
    return ConflictError(say("liveDocStale"), data={"doc_version": current_version})


async def overview_document(session: AsyncSession, project_id: uuid.UUID) -> Document:
    """The project's goals document, made empty (version 0) the first time anyone
    needs it: whoever opens, writes or comments on it needs its id first.

    Written back through the bare ``projects`` row, so the ``Project`` instance
    its caller holds is not refreshed and its ``overview_document_id`` stays as
    it was read (``ProjectOut`` does not carry the column either). Anyone who
    needs the pointer afterwards re-reads it — ``pointed_overview`` below is
    that read.
    """
    documents = Documents(session)
    seen = await _pointed_at(session, project_id)
    if seen is not None:
        doc = await documents.get(seen)
        if doc is not None:
            return doc
    # Two first readers at once must not make two goals documents. NO KEY
    # UPDATE, not FOR UPDATE: this runs inside a turn's assembly, and a full row
    # lock on the project stops every write whose foreign key names the project
    # (each block takes KEY SHARE on it) until the turn commits — which
    # deadlocked an archive that held its room's row and was writing its note.
    # Two creators still wait for each other.
    await session.execute(
        select(_projects.c.id)
        .where(_projects.c.id == project_id)
        .with_for_update(key_share=True)
    )
    seen = await _pointed_at(session, project_id)
    if seen is not None:
        doc = await documents.get(seen)
        if doc is not None:
            return doc
    doc = await documents.create(project_id=project_id)
    await session.execute(
        update(_projects)
        .where(_projects.c.id == project_id)
        .values(overview_document_id=doc.id)
    )
    if isinstance(session, AsyncSession):
        remember(
            session,
            ("overview_document_id", project_id),
            doc.id,
            tables=frozenset({"projects"}),
        )
    return doc


async def pointed_overview(
    session: AsyncSession, project_id: uuid.UUID
) -> Document | None:
    """The document the project names as its goals; None if none exists.

    This is how anyone else reads that pointer. Reading it off a ``Project``
    row instead goes through the identity map, and the pointer is written
    without the ORM (see ``overview_document`` above), so a row the same session
    loaded earlier still says None.
    """
    seen = await _pointed_at(session, project_id)
    return None if seen is None else await Documents(session).get(seen)


async def seed_overview(
    session: AsyncSession, project_id: uuid.UUID, content: str
) -> None:
    """Write a new project's goals from text it already had: what its creator
    said they wanted, or its 赛题. Author is `system`: the platform moved
    existing words, nobody wrote them here.

    The first version is the only one this writes: a document that has moved on
    (version 0 gone) is refused rather than overwritten. The journal is locked
    first, so two writers cannot both see version 0.
    """
    doc = await overview_document(session, project_id)
    journal = DocumentJournal(session)
    await journal.lock(doc.id)
    if doc.version != 0:
        raise _stale(doc.version)
    written = await Documents(session).write(
        doc, content, author="system", expected_version=0
    )
    if written is None:
        raise _stale(doc.version)
    await journal.append(
        document_id=doc.id,
        version=doc.version,
        content=content,
        actor="system",
        base_version=0,
    )


async def _pointed_at(session: AsyncSession, project_id: uuid.UUID) -> uuid.UUID | None:
    """Which document the project names as its goals, straight from the row."""
    if isinstance(session, AsyncSession):
        if session.autoflush:
            await session.flush()
        found, cached = recall(session, ("overview_document_id", project_id))
        if found:
            return cached
        found_proj, proj = recall(session, ("project", project_id))
        if found_proj and proj is not None:
            return proj.overview_document_id
    val = await session.scalar(
        select(_projects.c.overview_document_id).where(_projects.c.id == project_id)
    )
    if isinstance(session, AsyncSession):
        remember(
            session,
            ("overview_document_id", project_id),
            val,
            tables=frozenset({"projects"}),
        )
    return val
