"""Asking the room's AI teammate from the document (``app.api.doc_agent_box``).

A person who may comment on the document may ask; whether the answer may change
the document is the document's to say (an archived room or project is read-only),
not the browser's. The answer streams back as server-sent events, and runs in a
task of its own: a reader who leaves mid-answer loses the reading, and what the
teammate changed stays changed.
"""

import asyncio
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api import doc_agent, doc_agent_box
from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_handless_sessions
from app.api.doc_identity import human_operation_actor
from app.api.response import ok
from app.api.routes.living_docs import _frozen
from app.api.routes.topics import DbSession, _actor_in_place
from app.core.background import spawn
from app.core.errors import ForbiddenError, SystemBusyError, ValidationError
from app.core.redis import get_redis_client
from app.domain.agent.chat import ChatService
from app.domain.agent.harness.pi.handless import HandlessSessions
from app.domain.block.comment_threads import CommentThreads
from app.domain.block.notice_text import error_frame, say
from app.domain.living_doc.schemas import AgentAskIn
from app.domain.topic.services import TopicService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/topics", tags=["doc-agent"])

Chat = Annotated[ChatService, Depends(get_chat_service)]
Sessions = Annotated[HandlessSessions, Depends(get_handless_sessions)]


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _asker(db, resolver, topic_id: uuid.UUID):
    place = await TopicService(db).place_or_404(topic_id)
    actor = await _actor_in_place(resolver, place)
    await human_operation_actor(db, actor)
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    redis = get_redis_client()
    if redis is None:
        raise SystemBusyError(say("docAgentAskUnavailable"))
    return place, actor, redis


@router.post("/{topic_id}/doc/agent")
async def ask_agent(
    topic_id: uuid.UUID,
    body: AgentAskIn,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Chat,
    sessions: Sessions,
):
    """Ask, streamed as server-sent events: ``conversation`` (its id, first),
    ``queued``, ``working``, ``delta``, ``tool``, then ``done`` (``answer``,
    ``edits``, ``stopped``) or ``error``."""
    place, actor, redis = await _asker(db, resolver, topic_id)
    preset = doc_agent_box.PRESETS.get(body.preset or "") if body.preset else None
    if body.preset and preset is None:
        raise ValidationError(say("docAgentPresetNotFound"))
    if preset is not None and (preset.scope == "selection") != (
        body.selection is not None
    ):
        raise ValidationError(say("docAgentPresetNeedsSelection"))
    if body.conversation is not None:
        await doc_agent_box.owned(
            redis, body.conversation, asker=actor.handle, room_id=place.room_id
        )
    conversation = body.conversation or uuid.uuid4()
    may_edit = not await _frozen(db, place)
    selection = (
        doc_agent_box.Selection(
            body.selection.block, body.selection.start, body.selection.end
        )
        if body.selection is not None
        else None
    )
    await db.commit()

    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    queue.put_nowait(_sse("conversation", {"id": str(conversation)}))

    async def emit(event: str, data: dict) -> None:
        queue.put_nowait(_sse(event, data))

    async def run() -> None:
        try:
            await doc_agent_box.ask(
                chat,
                sessions,
                redis,
                emit,
                project_id=place.project_id,
                room_id=place.room_id,
                asker=actor.handle,
                conversation=conversation,
                preset=body.preset,
                text=body.text,
                selection=selection,
                may_edit=may_edit,
            )
        except Exception:  # noqa: BLE001 — the box is told; the log keeps why
            logger.warning("doc agent box failed", exc_info=True)
            await emit("error", error_frame(say("docAgentCantAnswer")))
        finally:
            queue.put_nowait(None)

    spawn(run(), name=f"doc-agent-box-{conversation}")

    async def stream():
        while (chunk := await queue.get()) is not None:
            yield chunk

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/{topic_id}/doc/agent/{conversation}/stop")
async def stop_agent(
    topic_id: uuid.UUID,
    conversation: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    sessions: Sessions,
) -> dict:
    """Stop the box's question: its wait for a turn, or the answer being
    written. What was already changed stays, for the box to undo."""
    place, actor, redis = await _asker(db, resolver, topic_id)
    await doc_agent_box.owned(
        redis, conversation, asker=actor.handle, room_id=place.room_id
    )
    await db.commit()
    await doc_agent_box.stop(
        redis, sessions, project_id=place.project_id, conversation=conversation
    )
    return ok({})


@router.post("/{topic_id}/doc/agent/{conversation}/reply/{thread_id}")
async def reply_in_thread(
    topic_id: uuid.UUID,
    conversation: uuid.UUID,
    thread_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    chat: Chat,
) -> dict:
    """Put the box's last answer into a comment thread the person started for
    it (「转成评论」), as the teammate's reply."""
    place, actor, redis = await _asker(db, resolver, topic_id)
    box = await doc_agent_box.owned(
        redis, conversation, asker=actor.handle, room_id=place.room_id
    )
    root = await CommentThreads(db).root(place.room_id, thread_id)
    if root.author != actor.handle:
        raise ForbiddenError("Only the thread you started takes this answer")
    if not box.answer:
        raise ValidationError(say("docAgentNoAnswerToComment"))
    bound = await doc_agent.bind(db, place.room_id)
    await db.commit()
    await doc_agent._reply(
        chat.session_factory,
        place.room_id,
        place.project_id,
        thread_id,
        bound,
        box.answer,
    )
    return ok({})
