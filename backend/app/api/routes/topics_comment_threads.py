"""Room document replies and resolution share member authorization and receipts."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_work_runner
from app.api.doc_comment_mentions import hand_to_agent, mentioned_seat
from app.api.doc_identity import operation_actor
from app.api.response import ok, page
from app.api.routes.topics import DbSession, _actor_in_place
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.comment_schemas import ReplyIn, ThreadMutation
from app.domain.block.comment_threads import CommentThreads
from app.domain.living_doc.services import DocumentJournal
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["doc-comments"])


async def member_in_room(db, resolver, topic_id):
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    identity = await operation_actor(db, actor)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    return place, actor, identity


@router.get("/{topic_id}/comments/threads")
async def list_threads(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    place, _, _ = await member_in_room(db, resolver, topic_id)
    await DocumentJournal(db).lock(place.room_id)
    service = CommentThreads(db)
    items = [
        await service.describe(comment, summary=True)
        for comment in await service.roots(place.room_id)
    ]
    await db.commit()
    return ok(page(items, len(items)))


@router.get("/{topic_id}/comments/{comment_id}/thread")
async def read_thread(
    topic_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    place, _, _ = await member_in_room(db, resolver, topic_id)
    await DocumentJournal(db).lock(place.room_id)
    service = CommentThreads(db)
    result = await service.describe(await service.root(place.room_id, comment_id))
    await db.commit()
    return ok(result)


async def mutate(db, resolver, topic_id, comment_id, body, action, hand_off=None):
    """Apply one thread mutation once per operation id. ``hand_off(place,
    actor)`` runs after a first application commits, never on a replay."""
    place, actor, identity = await member_in_room(db, resolver, topic_id)
    journal = DocumentJournal(db)
    operation = await journal.claim(
        room_id=place.room_id,
        actor=identity,
        action="comment-thread",
        operation_id=body.operation_id,
        payload={
            "comment_id": str(comment_id),
            "action": action,
            **body.model_dump(mode="json", exclude={"operation_id"}),
        },
    )
    if operation.receipt is not None:
        return operation.receipt
    result = await CommentThreads(db).mutate(
        room_id=place.room_id,
        project_id=place.project_id,
        comment_id=comment_id,
        author=actor.handle,
        expected_revision=body.expected_revision,
        action=action,
        content=body.content if isinstance(body, ReplyIn) else None,
    )
    receipt = ok(result)
    await journal.finish(operation, receipt)
    after = await hand_off(db, place, actor) if hand_off else None
    await db.commit()
    if after is not None:
        after()
    return receipt


@router.post("/{topic_id}/comments/{comment_id}/replies")
async def reply(
    topic_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ReplyIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    runner: Annotated[AgentWorkRunner, Depends(get_work_runner)],
) -> dict:
    """A reply that @-mentions the room's agent hands the thread to it
    (``app.api.doc_comment_mentions``)."""

    async def hand_off(db, place, actor):
        seat = await mentioned_seat(db, place, actor, body.content)
        if seat is None:
            return None
        root = await CommentThreads(db).root(place.room_id, comment_id)
        return lambda: hand_to_agent(
            chat,
            runner,
            place=place,
            actor=actor,
            seat=seat,
            thread_id=comment_id,
            content=body.content,
            quote=root.anchor_quote,
        )

    return await mutate(db, resolver, topic_id, comment_id, body, "reply", hand_off)


@router.post("/{topic_id}/comments/{comment_id}/resolve")
async def resolve(
    topic_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ThreadMutation,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    return await mutate(db, resolver, topic_id, comment_id, body, "resolve")


@router.post("/{topic_id}/comments/{comment_id}/reopen")
async def reopen(
    topic_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ThreadMutation,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    return await mutate(db, resolver, topic_id, comment_id, body, "reopen")
