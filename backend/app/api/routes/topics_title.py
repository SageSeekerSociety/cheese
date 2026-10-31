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
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.room_task_rows import task_out
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say
from app.domain.agent.staleness import announce_stale
from app.domain.room_task import heard
from app.domain.room_task.place import place_or_404
from app.domain.room_task.services import TaskService
from app.domain.room_task.title_width import TITLE_WIDTH, title_fits, title_width
from app.domain.topic.schemas import TopicOut
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/{topic_id}/title", dependencies=[ROUTE_DECIDES])
async def set_title(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """给这个地方起/改标题: a person renaming a channel or a task, or a task's
    own session naming it (`cheese_room(action=rename)`, only where the platform cannot).

    A channel is renamed by whoever manages it: its own admin, or someone whose
    role manages every channel (#3830). Being able to reach the room at all is
    settled above (`authorize_topic`) — an outsider to a private channel is
    still told it does not exist.

    A task's id names the task: a person taking part in it renames it, and that
    title is final; its own session's title the platform may still change
    (`room_task/naming.py`).
    """
    place = await place_or_404(db, topic_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, room=place.room
    )
    title = (body.get("title") or "").strip()
    if not title:
        raise ValidationError(say("titleRequired"))
    task = place.task
    if task is not None:
        own_session = resolver.credential_conversation() == task.id
        if not own_session and not TaskService.takes_part(task, actor.handle):
            raise ForbiddenError(say("taskParticipantsOnly"))
        # A task's own session names it to the same width a teammate creating
        # one is held to (`cheese_room(action=rename)`): told, not cut. A person renaming a
        # task is left to `title[:80]` — the width rule is about what an AI
        # teammate writes, and a person can be trusted to know what they meant.
        if own_session and not title_fits(title):
            raise UnprocessableEntityError(
                say("taskTitleTooLong", limit=TITLE_WIDTH, now=title_width(title))
            )
        previous = task.title
        TaskService(db).rename(
            task,
            title[:80],
            by=actor.handle,
            by_person=not own_session,
        )
        if not own_session and task.title != previous:
            # Its own session named it, and read that in the tool's reply.
            await heard.inform(
                db,
                task,
                heard.task_renamed(
                    by=actor.handle, title=task.title, previous=previous
                ),
            )
        # The task as its page holds it, board line included: the page writes
        # this answer over what it has, and a task without its line drew no
        # header until the next reload.
        out = await task_out(db, task, me=actor.handle or "")
        await db.commit()
        return ok(out)
    # A channel's name is one of its settings, like its 说明 and who sees it
    # (`topics_channel`): whoever manages the channel changes it.
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("signInForRoom"))
    if not await TopicMemberService(db).manages(place.room, actor.handle):
        raise ForbiddenError(say("channelManagerOnly"))
    place.room.title = title[:80]
    await db.flush()
    out = TopicOut.model_validate(place.room).model_dump(mode="json")
    await db.commit()
    await announce_stale(place.room_id, "topics", id=place.room_id)
    return ok(out)
