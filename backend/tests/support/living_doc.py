"""Setting up a living document in a test.

A document version is recorded one way only: a store from the collaboration
service. Tests that need a document to exist record one the same way.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.documents import DocumentWriter
from app.domain.topic.doc_change import summarize_doc_change
from app.domain.topic.services import TopicService


async def write_doc(
    session: AsyncSession,
    room_id: uuid.UUID,
    content: str,
    actor: str = "alice",
    *,
    quiet: bool = False,
):
    """Record ``content`` as the room's next document version, by ``actor``.
    ``quiet``: the room is not told (no "编辑了文档" line)."""
    topics = TopicService(session)
    place = await topics.place_or_404(room_id)
    doc = await topics.room_doc(place.room_id, place.project_id)
    return await DocumentWriter(session, summarize_doc_change).record(
        doc, content=content, actors=[actor], quiet=quiet
    )
