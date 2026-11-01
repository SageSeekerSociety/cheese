"""A document's node tree and its comment threads.

``GET /documents/{id}/nodes`` is the document's top-level blocks in document
order. ``POST /documents/{id}/comments`` starts a thread; which words it is
about is marked in the shared document by the commenter's editor, and the
comment keeps the quoted words for display. Replies, resolving and reopening
are applied once per operation id, under the document's lock.

A comment, or a reply, that @-mentions the document's agent hands the thread to
it (``app.domain.agent.document.thread``). A person it @s is told, once for that
comment, when they can open the document; whom it named who cannot is said back
to the writer alone (``mentioned_outside``), as a message in a channel does
(``app.domain.agent.mentions.announce_comment_mentions``).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_consumptions, get_session_host
from app.api.doc_access import Reached, asked_of, reach, require_writable
from app.api.doc_identity import operation_actor
from app.api.request_deps import DbSession
from app.api.response import ok, page
from app.api.write_access import ROUTE_DECIDES
from app.core.errors import SystemBusyError, ValidationError
from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.agent.chat import ChatService
from app.domain.agent.document import thread
from app.domain.agent.document.question import answering
from app.domain.agent.document.thread import hand_to_agent, mentioned_seat
from app.domain.agent.mentions import (
    CommentMentions,
    announce_comment_mentions,
    comment_notified,
)
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.agent.session_host.host import SessionHost
from app.domain.agent_instance.adapters import project_team
from app.domain.living_doc import collab
from app.domain.living_doc.comment_schemas import CommentIn, ReplyIn, ThreadMutation
from app.domain.living_doc.comments import CommentThreads, OnDocument, comment_out
from app.domain.living_doc.schemas import node_out
from app.domain.living_doc.services import DocumentJournal, Documents
from app.domain.mentions import canonicalize_refs
from app.domain.project_permissions import Permission

router = APIRouter(prefix="/documents", tags=["document-comments"])

Chat = Annotated[ChatService, Depends(get_chat_service)]
Questions = Annotated[Consumptions, Depends(get_consumptions)]


@router.get("/{document_id}/nodes")
async def list_nodes(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """The document's top-level blocks, in document order."""
    doc = (await reach(db, resolver, document_id)).doc
    items = [node_out(n, doc) for n in await Documents(db).nodes(doc)]
    return ok(page(items, len(items)))


@router.post("/{document_id}/comments", dependencies=[ROUTE_DECIDES])
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
    # Says why: an archived project, a closed task, or someone not working it.
    await require_writable(db, reached, Permission.comment)
    content = body.content.strip()
    if not content:
        raise ValidationError(say("commentEmpty"))
    comment = await CommentThreads(db).open(
        OnDocument(reached.doc.id),
        author=reached.actor.handle,
        content=await _as_stored(db, reached, content),
        quote=(body.quote or "").strip() or None,
    )
    told = await _announce(db, reached, comment)
    payload = {**comment_out(comment), "notified": told.notified, **_outside(told)}
    asked = asked_of(reached)
    seat = (
        await mentioned_seat(db, asked, reached.actor, content)
        if asked is not None
        else None
    )
    await db.commit()
    await collab.tell(reached.doc.id, {"type": "state", "resource": "comments"})
    if seat is not None and asked is not None:
        hand_to_agent(
            chat,
            questions,
            asked=asked,
            actor=reached.actor,
            seat=seat,
            thread_id=comment.id,
        )
    return ok(payload)


async def _as_stored(db, reached: Reached, content: str) -> str:
    """A comment's text as stored: a friendly @名字 becomes the person's
    ``<@handle>`` token, as every other text a person writes does."""
    if reached.doc.project_id is None:
        return content
    return await canonicalize_refs(
        db,
        reached.doc.project_id,
        content,
        team=project_team,
        exclude_topic_id=reached.place.room_id if reached.place else None,
    )


async def _announce(db, reached: Reached, comment) -> CommentMentions:
    place = reached.place
    return await announce_comment_mentions(
        db,
        reached.doc.project_id,
        place.room if place is not None else None,
        comment,
        title=reached.doc.title or (place.title if place is not None else ""),
        document_id=reached.doc.id,
        task_id=reached.task.id if reached.task is not None else None,
    )


def _outside(told: CommentMentions) -> dict:
    """Said to the writer alone, never stored: whom it named who cannot open
    the document."""
    return {"mentioned_outside": told.outside.as_json()} if told.outside else {}


async def _with_notified(db, described: dict) -> dict:
    """Each comment of a thread with whom its @s told."""
    return (await _with_notified_many(db, [described]))[0]


async def _with_notified_many(db, items: list[dict]) -> list[dict]:
    if not items:
        return []
    comments = [
        c
        for described in items
        for c in [described["comment"], *(r["comment"] for r in described["replies"])]
    ]
    told = await comment_notified(db, [uuid.UUID(c["id"]) for c in comments])
    for c in comments:
        c["notified"] = told.get(c["id"], [])
    return items


async def _member(db, resolver, document_id: uuid.UUID) -> Reached:
    return await reach(db, resolver, document_id, enforce=True)


@router.get("/{document_id}/comments/threads")
async def list_threads(
    document_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """Every thread on the document with its replies, oldest first, and
    whether the agent is answering it now (``queued`` while it waits for a
    free session, ``working`` while it answers)."""
    reached = await _member(db, resolver, document_id)
    await DocumentJournal(db).lock(reached.doc.id)
    service = CommentThreads(db)
    roots = await service.roots(OnDocument(reached.doc.id))
    items = await _with_notified_many(db, await service.describe_many(roots))
    await db.commit()
    states = (
        await answering(
            reached.doc.project_id,
            [uuid.UUID(item["comment"]["id"]) for item in items],
        )
        if reached.doc.project_id is not None and items
        else {}
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
    reached = await _member(db, resolver, document_id)
    await DocumentJournal(db).lock(reached.doc.id)
    service = CommentThreads(db)
    result = await _with_notified(
        db,
        await service.describe(
            await service.root(OnDocument(reached.doc.id), comment_id)
        ),
    )
    await db.commit()
    return ok(result)


async def _mutate(db, resolver, document_id, comment_id, body, action, hand_off=None):
    """Apply one thread mutation once per operation id. ``hand_off(reached)``
    runs after a first application commits, never on a replay."""
    reached = await _member(db, resolver, document_id)
    identity = await operation_actor(db, reached.actor)
    writable_error: Exception | None = None
    try:
        await require_writable(db, reached, Permission.comment)
    except Exception as exc:
        writable_error = exc
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
    if writable_error is not None:
        raise writable_error
    reply = action == "reply"
    assert not reply or isinstance(body, ReplyIn)
    result = await CommentThreads(db).mutate(
        OnDocument(reached.doc.id),
        comment_id=comment_id,
        author=reached.actor.handle,
        expected_revision=body.expected_revision,
        action=action,
        content=await _as_stored(db, reached, body.content) if reply else None,
        locked=True,
    )
    told = None
    if reply:
        added = await CommentThreads(db).comment(
            uuid.UUID(result["replies"][-1]["comment"]["id"])
        )
        told = await _announce(db, reached, added)
    result = await _with_notified(db, result)
    receipt = ok(result)
    await journal.finish(operation, receipt)
    after = await hand_off(reached) if hand_off else None
    await db.commit()
    await collab.tell(reached.doc.id, {"type": "state", "resource": "comments"})
    if after is not None:
        after()
    # The writer's own answer, not the operation's stored receipt: a replay
    # says nothing of who was outside.
    return (
        {**receipt, "data": {**receipt["data"], **_outside(told)}} if told else receipt
    )


@router.post(
    "/{document_id}/comments/{comment_id}/replies", dependencies=[ROUTE_DECIDES]
)
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
        if asked is None:
            return None
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


@router.post(
    "/{document_id}/comments/{comment_id}/resolve", dependencies=[ROUTE_DECIDES]
)
async def resolve(
    document_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ThreadMutation,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    return await _mutate(db, resolver, document_id, comment_id, body, "resolve")


@router.post(
    "/{document_id}/comments/{comment_id}/reopen", dependencies=[ROUTE_DECIDES]
)
async def reopen(
    document_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: ThreadMutation,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    return await _mutate(db, resolver, document_id, comment_id, body, "reopen")


@router.post(
    "/{document_id}/comments/{comment_id}/agent/stop", dependencies=[ROUTE_DECIDES]
)
async def stop_agent(
    document_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    sessions: Annotated[SessionHost, Depends(get_session_host)],
) -> dict:
    """Stop the agent answering this thread: its wait, or the answer being
    written. Its reply keeps what was written, marked as stopped."""
    reached = await _member(db, resolver, document_id)
    # The thread named is this document's own, as for every other thread route.
    await CommentThreads(db).root(OnDocument(reached.doc.id), comment_id)
    await db.commit()
    redis = get_redis_client()
    if redis is None:
        raise SystemBusyError(say("docAgentAskUnavailable"))
    if reached.doc.project_id is not None:
        await thread.stop(
            redis, sessions, project_id=reached.doc.project_id, thread_id=comment_id
        )
    return ok({})
