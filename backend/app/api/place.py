"""Who may read a project's own records: a member, or 芝士 working in one of its rooms.

芝士's credential is minted for one turn in one place, so a bare
`authorize_project` refuses it (403) even though the token is valid and the
project is right. The caller names its place with `?topic=`, and the place is
what gets authorized — which is also what keeps it inside that project.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.core.errors import ForbiddenError, ValidationError
from app.domain.identity.actor import Actor
from app.domain.room_task.place import Place
from app.domain.topic.services import TopicService


async def authorized_place(
    db: AsyncSession,
    resolver: ActorResolver,
    project_id: uuid.UUID,
    topic_raw: str,
) -> tuple[Place, Actor] | None:
    """Resolve and authorize the caller-named place, when present, and say
    who is calling — the pool a memory goes to is that caller's.

    A place, not a room: `cheese_remember` is run by whoever is doing the work,
    and that is usually a thread.
    """
    if not topic_raw:
        return None
    try:
        topic_id = uuid.UUID(topic_raw)
    except ValueError as exc:
        raise ValidationError("topic 不是合法的话题 id") from exc
    place = await TopicService(db).place_or_404(topic_id)
    if place.project_id != project_id:
        raise ForbiddenError("这个话题不属于 URL 中的项目")
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=place.room_id, project_id=project_id
    )
    await resolver.authorize_topic(actor, project_id=project_id, topic_id=place.room_id)
    return place, actor


async def project_reader(
    db: AsyncSession,
    resolver: ActorResolver,
    project_id: uuid.UUID,
    topic_raw: str,
) -> Actor:
    """The caller, once it is allowed to read this project's records."""
    placed = await authorized_place(db, resolver, project_id, topic_raw)
    if placed is not None:
        return placed[1]
    actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    return actor
