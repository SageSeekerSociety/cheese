"""The living doc's node tree, and new comments written on it.

``GET /topics/{topic_id}/docs`` is the document's structured node tree in
document order. ``POST /topics/{topic_id}/comments`` starts a comment thread;
which words it is about is marked in the shared document by the commenter's
editor, and the comment keeps the quoted words for display. Reading threads,
replying and resolving live in ``topics_comment_threads``.

``BlockRepository``, ``AuthorType`` and ``BlockKind`` are imported from
topics.py, which already owns those edges, so neither import ratchet grows.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_consumptions
from app.api.response import ok, page
from app.api.routes.living_docs import _frozen
from app.api.routes.topics import (
    AuthorType,
    BlockKind,
    BlockRepository,
    DbSession,
    _actor_in_place,
)
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.agent.chat import ChatService
from app.domain.agent.document.thread import hand_to_agent, mentioned_seat
from app.domain.agent.runtime import announce_stale
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.block.comment_threads import CommentThreads
from app.domain.block.schemas import BlockOut
from app.domain.living_doc.schemas import node_out
from app.domain.living_doc.services import DocumentJournal
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/{topic_id}/docs")
async def list_topic_docs(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """The room document's top-level blocks, in document order."""
    topics = TopicService(db)
    place = await topics.place_or_404(topic_id)
    await _actor_in_place(resolver, place)
    doc = await topics.doc_of_room(place.room_id)
    items = [node_out(n, doc) for n in await topics.doc_nodes(doc)] if doc else []
    return ok(page(items, len(items)))


@router.post("/{topic_id}/comments")
async def add_comment(
    topic_id: uuid.UUID,
    body: dict,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Annotated[ChatService, Depends(get_chat_service)],
    questions: Annotated[Consumptions, Depends(get_consumptions)],
) -> dict:
    """Start a comment thread on the words ``quote`` (or on the whole
    document without one). A person's comment that @-mentions the room's agent
    hands it to that agent (``app.domain.agent.document.thread``); any other
    comment starts nothing. An archived room's document is frozen and takes no
    comments."""
    place = await TopicService(db).place_or_404(topic_id)
    if await _frozen(db, place):
        raise ValidationError(say("commentDocFrozen"))
    doc = await TopicService(db).room_doc(place.room_id, place.project_id)
    await DocumentJournal(db).lock(doc.id)
    content = (body.get("content") or "").strip()
    if not content:
        raise ValidationError(say("commentEmpty"))
    repo = BlockRepository(db)
    # Kept for display next to the comment; bounded so a runaway selection
    # can't bloat the row.
    quote = (body.get("quote") or "").strip() or None
    if quote and len(quote) > 500:
        quote = quote[:500]
    actor = await resolver.resolve(topic_id=place.room_id, project_id=place.project_id)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    author = actor.handle
    comment = await repo.add(
        project_id=place.project_id,
        # The place id, not the room's: `add` splits it, and handing it the room
        # would post a thread's comment onto the room for everyone to read.
        topic_id=topic_id,
        author=author,
        author_type=AuthorType.participant,
        content=content,
        kind=BlockKind.comment,
        anchor_quote=quote,
    )
    await CommentThreads(db).capture(comment)
    payload = BlockOut.model_validate(comment).model_dump(mode="json")
    seat = await mentioned_seat(db, place, actor, content)
    await db.commit()
    await announce_stale(place.room_id, "comments")
    if seat is not None:
        hand_to_agent(
            chat,
            questions,
            place=place,
            actor=actor,
            seat=seat,
            thread_id=comment.id,
        )
    return ok(payload)
