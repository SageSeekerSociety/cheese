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
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.room_task.place import Place
from app.domain.topic.models import room_ref
from app.domain.topic.repositories import TopicRepository
from app.domain.topic.services import TopicService


async def authorized_place(
    db: AsyncSession,
    resolver: ActorResolver,
    project_id: uuid.UUID,
    topic_raw: str,
) -> tuple[Place, Actor] | None:
    """Resolve and authorize the caller-named place, when present, and say
    who is calling — the pool a memory goes to is that caller's.

    A place, not a room: the caller is whoever is doing the work, and that is
    usually a thread.
    """
    if not topic_raw:
        return None
    try:
        topic_id = uuid.UUID(topic_raw)
    except ValueError as exc:
        raise ValidationError(say("topicIdInvalid")) from exc
    place = await TopicService(db).place_or_404(topic_id)
    if place.project_id != project_id:
        raise ForbiddenError(say("topicNotInUrlProject"))
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=project_id
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
    actor = await resolver.resolve(project_id=project_id)
    await resolver.authorize_project(actor, project_id=project_id)
    return actor


async def readable_rooms(
    db: AsyncSession, resolver: ActorResolver, actor: Actor, project_id: uuid.UUID
) -> dict[uuid.UUID, dict]:
    """这个项目里 `actor` 读得了的房间，各自在别的清单里写成的样子（`room_ref`）。

    一样东西要说出它出自哪个房间时用：读不了的房间（别人的私聊）连名字也不该从
    旁边漏出去，所以只给读得了的那些。"""
    rooms, _, _ = await TopicService(db).list_for_project(
        project_id, viewer=actor.handle if actor.authenticated else None
    )
    readable = await resolver.readable_topic_ids(
        actor, project_id=project_id, topics=rooms
    )
    return {room.id: room_ref(room) for room in rooms if room.id in readable}


async def rooms_seen(
    db: AsyncSession, resolver: ActorResolver, actor: Actor, project_id: uuid.UUID
) -> set[uuid.UUID]:
    """The ids of this project's channels whose work a project-wide list shows
    ``actor``, once it may read the project: every public channel, and the
    private channels it sits in. On the development credential alone, which
    names nobody (``ActorResolver.on_the_dev_credential``), every channel."""
    viewer = None if resolver.on_the_dev_credential(actor) else actor.handle
    rooms = await TopicRepository(db).list_for_project(project_id, only_seen_by=viewer)
    return {room.id for room in rooms}


async def live_rooms_seen(
    db: AsyncSession, resolver: ActorResolver, actor: Actor, project_id: uuid.UUID
) -> set[uuid.UUID]:
    """``rooms_seen`` without the archived channels: what is still going on."""
    viewer = None if resolver.on_the_dev_credential(actor) else actor.handle
    rooms = await TopicRepository(db).list_for_project(project_id, only_seen_by=viewer)
    return {room.id for room in rooms if str(room.status) != "archived"}


async def channels_unseen(
    db: AsyncSession, resolver: ActorResolver, actor: Actor, project_id: uuid.UUID
) -> set[uuid.UUID]:
    """This project's private channels ``actor`` is not in — the complement of
    ``rooms_seen`` among channels. What was made in them is not shown to it,
    even in a project-wide list that does not name the channel."""
    if resolver.on_the_dev_credential(actor):
        return set()
    seen = await rooms_seen(db, resolver, actor, project_id)
    rooms = await TopicRepository(db).list_for_project(project_id)
    return {room.id for room in rooms if room.members_only and room.id not in seen}


async def task_conversation(
    db: AsyncSession, resolver: ActorResolver, conversation_id: uuid.UUID
):
    """The task this conversation id names, with who is calling — whoever may
    see its room; 404 when the id is a room's or nobody's."""
    from app.core.errors import NotFoundError

    place = await TopicService(db).place_or_404(conversation_id)
    if place.task is None:
        raise NotFoundError(say("taskNotFound"))
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    return place, actor, place.task
