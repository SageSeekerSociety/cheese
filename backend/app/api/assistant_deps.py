"""How a person's 芝士 is let into a project's things: as the person opening
their pages would be.

Both the question (`routes/assistant.py`, the page it is asked from) and the
reads it makes (`routes/assistant_agent.py`) judge a project's page with this
one class, and route modules do not import each other.
"""

from __future__ import annotations

import uuid

from app.api.auth import ActorResolver
from app.api.doc_access import reach
from app.domain.room_task.place import place_or_404
from app.domain.topic.services import TopicService


class ProjectPages:
    """Whether the person may open a project's page, judged by the request's
    own resolver as opening that page is (``pages.PageAccess``,
    ``reading.Access``) — as a read: asking and reading only look."""

    def __init__(self, db, resolver: ActorResolver) -> None:
        self._db = db
        self._resolver = resolver.for_reading()

    async def topic(self, topic_id: uuid.UUID):
        place = await place_or_404(self._db, topic_id)
        actor = await self._resolver.resolve(
            topic_id=place.conversation_id, project_id=place.project_id
        )
        await self._resolver.authorize_topic(
            actor, project_id=place.project_id, room=place.room
        )
        return place

    async def document(self, document_id: uuid.UUID) -> None:
        await reach(self._db, self._resolver, document_id)

    async def project(self, project_id: uuid.UUID) -> None:
        actor = await self._resolver.resolve(project_id=project_id)
        await self._resolver.authorize_project(actor, project_id=project_id)

    async def channels(self, project_id: uuid.UUID) -> list:
        actor = await self._resolver.resolve(project_id=project_id)
        await self._resolver.authorize_project(actor, project_id=project_id)
        topics, _, _ = await TopicService(self._db).list_for_project(
            project_id, viewer=actor.handle
        )
        readable = await self._resolver.readable_topic_ids(
            actor, project_id=project_id, topics=topics
        )
        return [t for t in topics if t.id in readable]
