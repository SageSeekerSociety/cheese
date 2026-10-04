"""A document's node tree and its comment threads.

``GET /documents/{id}/nodes`` is the document's top-level blocks in document
order. ``POST /documents/{id}/comments`` starts a thread; which words it is
about is marked in the shared document by the commenter's editor, and the
comment keeps the quoted words for display. Replies, resolving and reopening
are applied once per operation id, under the document's lock.

A comment, or a reply, that @-mentions the document's agent hands the thread to
it (``app.domain.agent.document.thread``); any other comment starts nothing.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_consumptions, get_session_host
from app.api.doc_access import Reached, frozen, reach
from app.api.doc_identity import operation_actor
from app.api.response import ok, page
from app.api.routes.topics import DbSession
from app.core.errors import SystemBusyError, ValidationError
from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.agent.chat import ChatService
from app.domain.agent.document import thread
from app.domain.agent.document.question import Asked, answering
from app.domain.agent.document.thread import hand_to_agent, mentioned_seat
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.agent.session_host.host import SessionHost
from app.domain.living_doc import collab
from app.domain.living_doc.comment_schemas import CommentIn, ReplyIn, ThreadMutation
from app.domain.living_doc.comments import CommentThreads, comment_out
from app.domain.living_doc.schemas import node_out
from app.domain.living_doc.services import DocumentJournal, Documents

router = APIRouter(prefix="/documents", tags=["document-comments"])

Chat = Annotated[ChatService, Depends(get_chat_service)]
Questions = Annotated[Consumptions, Depends(get_consumptions)]


def asked_of(reached: Reached) -> Asked:
    doc = reached.doc
    return Asked(project_id=doc.project_id, document_id=doc.id, room_id=doc.room_id)


@router.get("/{document_id}/nodes")
async def list_nodes(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The document's top-level blocks, in document order."""
    doc = (await reach(db, resolver, document_id)).doc
    items = [node_out(n, doc) for n in await Documents(db).nodes(doc)]
    return ok(page(items, len(items)))


@router.post("/{document_id}/comments")
async def add_comment(
    document_id: uuid.UUID,
    body: CommentIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Chat,
    questions: Questions,
) -> dict:
    """Start a comment thread on the words ``quote`` (or on the whole document
    without one). An archived room's document, and every document of an
    archived project, is frozen and takes no comments."""
    reached = await reach(db, resolver, document_id)
    if await frozen(db, reached):
        raise ValidationError(say("commentDocFrozen"))
    content = body.content.strip()
    if not content:
        raise ValidationError(say("commentEmpty"))
    comment = await CommentThreads(db).open(
        reached.doc,
        author=reached.actor.handle,
        content=content,
        quote=(body.quote or "").strip() or None,
    )
    payload = comment_out(comment)
    asked = asked_of(reached)
    seat = await mentioned_seat(db, asked, reached.actor, content)
    await db.commit()
    await collab.tell(reached.doc.id, {"type": "state", "resource": "comments"})
    if seat is not None:
        hand_to_agent(
            chat,
            questions,
            asked=asked,
            actor=reached.actor,
            seat=seat,
            thread_id=comment.id,
        )
    return ok(payload)


async def _member(db, resolver, document_id: uuid.UUID) -> tuple[Reached, str]:
    reached = await reach(db, resolver, document_id, enforce=True)
    return reached, await operation_actor(db, reached.actor)


@router.get("/{document_id}/comments/threads")
async def list_threads(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Every thread on the document with its replies, oldest first, and
    whether the agent is answering it now (``queued`` while it waits for a
    free session, ``working`` while it answers)."""
    reached, _ = await _member(db, resolver, document_id)
    await DocumentJournal(db).lock(reached.doc.id)
    service = CommentThreads(db)
    items = [await service.describe(c) for c in await service.roots(reached.doc.id)]
    await db.commit()
    states = await answering(
        reached.doc.project_id, [uuid.UUID(item["comment"]["id"]) for item in items]
    )
    for item in items:
        item["answering"] = states.get(item["comment"]["id"])
    return ok(page(items, len(items)))


@router.get("/{document_id}/comments/{comment_id}/thread")
async def read_thread(
    document_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    reached, _ = await _member(db, resolver, document_id)
    await DocumentJournal(db).lock(reached.doc.id)
    service = CommentThreads(db)
    result = await service.describe(await service.root(reached.doc.id, comment_id))
    await db.commit()
    return ok(result)


async def _mutate(db, resolver, document_id, comment_id, body, action, hand_off=None):
    """Apply one thread mutation once per operation id. ``hand_off(reached)``
    runs after a first application commits, never on a replay."""
    reached, identity = await _member(db, resolver, document_id)
    journal = DocumentJournal(db)
    operation = await journal.claim(
        document_id=reached.doc.id,
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
    if await frozen(db, reached):
        raise ValidationError(say("commentDocFrozen"))
    result = await CommentThreads(db).mutate(
        reached.doc,
        comment_id=comment_id,
        author=reached.actor.handle,
        expected_revision=body.expected_revision,
        action=action,
        content=body.content if isinstance(body, ReplyIn) else None,
    )
    receipt = ok(result)
    await journal.finish(operation, receipt)
    after = await hand_off(reached) if hand_off else None
    await db.commit()
    await collab.tell(reached.doc.id, {"type": "state", "resource": "comments"})
    if after is not None:
        after()
    return receipt


@router.post("/{document_id}/comments/{comment_id}/replies")
async def reply(
    document_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ReplyIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Chat,
    questions: Questions,
) -> dict:
    """A reply that @-mentions the document's agent hands the thread to it."""

    async def hand_off(reached: Reached):
        asked = asked_of(reached)
        seat = await mentioned_seat(db, asked, reached.actor, body.content)
        if seat is None:
            return None
        return lambda: hand_to_agent(
            chat,
            questions,
            asked=asked,
            actor=reached.actor,
            seat=seat,
            thread_id=comment_id,
        )

    return await _mutate(db, resolver, document_id, comment_id, body, "reply", hand_off)


@router.post("/{document_id}/comments/{comment_id}/resolve")
async def resolve(
    document_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ThreadMutation,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    return await _mutate(db, resolver, document_id, comment_id, body, "resolve")


@router.post("/{document_id}/comments/{comment_id}/reopen")
async def reopen(
    document_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ThreadMutation,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    return await _mutate(db, resolver, document_id, comment_id, body, "reopen")


@router.post("/{document_id}/comments/{comment_id}/agent/stop")
async def stop_agent(
    document_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    sessions: Annotated[SessionHost, Depends(get_session_host)],
) -> dict:
    """Stop the agent answering this thread: its wait, or the answer being
    written. Its reply keeps what was written, marked as stopped."""
    reached, _ = await _member(db, resolver, document_id)
    await db.commit()
    redis = get_redis_client()
    if redis is None:
        raise SystemBusyError(say("docAgentAskUnavailable"))
    await thread.stop(
        redis, sessions, project_id=reached.doc.project_id, thread_id=comment_id
    )
    return ok({})
