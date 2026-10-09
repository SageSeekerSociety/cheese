"""A place's title: a person renaming a channel or a task, or a task's own
session naming it.

Split out of `app/api/routes/topics.py`. `Topic` is not imported here: the C2
contract in `.importlinter` ratchets (route module, model module) pairs, so the
room is reached through `TopicService.place_or_404`. The module mounts itself:
`app.main._discover_routers` includes every module-level `APIRouter` under
`app.api.routes`.
"""

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.topics import DbSession
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.agent.staleness import announce_stale
from app.domain.room_task.schemas import TaskOut
from app.domain.room_task.services import TaskService
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/{topic_id}/title")
async def set_title(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """给这个地方起/改标题: a person renaming a channel or a task, or a task's
    own session naming it (`cheese_title`, only where the platform cannot).

    A channel is renamed by anyone who can be in it: which channel is called what
    is the room's own business, not its creator's alone. Being able to reach the
    room at all is settled above (`authorize_topic`), which is the same answer a
    reader of the channel gets — and an outsider to a private channel is still
    told it does not exist.

    A task's id names the task: a person taking part in it renames it, and that
    title is final; its own session's title the platform may still change
    (`room_task/naming.py`).
    """
    place = await TopicService(db).place_or_404(topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    title = (body.get("title") or "").strip()
    if not title:
        raise ValidationError(say("titleRequired"))
    task = place.task
    if task is not None:
        own_session = resolver.credential_conversation() == task.id
        if not own_session and not TaskService.takes_part(task, actor.handle):
            raise ForbiddenError(say("taskParticipantsOnly"))
        TaskService(db).rename(
            task,
            title[:80],
            by=actor.handle,
            by_person=not own_session,
        )
        out = TaskOut.model_validate(task).model_dump(mode="json")
        await db.commit()
        await announce_stale(place.room_id, "topics", id=place.room_id)
        # The task's own page listens on the task's conversation, but the row
        # that changed is the ROOM's in the sidebar — a task id is not a row.
        await announce_stale(task.id, "topics", id=place.room_id)
        return ok(out)
    # Renaming a channel takes no more than being able to be in it — the rest of
    # its settings (说明、可见范围) still belong to whoever manages it
    # (`topics_channel`). What is left to ask here is that the asker is a
    # verified person and not, say, an unauthenticated caller on a deployment
    # with the access check turned off.
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("signInForRoom"))
    place.room.title = title[:80]
    await db.flush()
    out = TopicOut.model_validate(place.room).model_dump(mode="json")
    await db.commit()
    await announce_stale(place.room_id, "topics", id=place.room_id)
    return ok(out)
