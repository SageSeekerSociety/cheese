"""Asking the room's AI teammate from the document (``app.domain.agent.document.box``).

A person who may comment on the document may ask; whether the answer may change
the document is the document's to say (an archived room or project is read-only),
not the browser's. The answer streams back as server-sent events. Once asked, it
is read to its end by whichever backend is up (``session_host.consumptions``):
a reader who loses the stream reads the rest of it from where it was, and what
the teammate changed stays changed.
"""

import asyncio
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.auth import ActorResolverDep
from app.api.deps import get_chat_service, get_consumptions, get_session_host
from app.api.doc_identity import human_operation_actor
from app.api.response import ok
from app.api.routes.living_docs import _frozen
from app.api.routes.topics import DbSession, _actor_in_place
from app.core.background import spawn
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    SystemBusyError,
    ValidationError,
)
from app.core.redis import get_redis_client
from app.core.sentences import error_frame, say
from app.domain.agent.chat import ChatService
from app.domain.agent.document import box, question, thread
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.agent.session_host.host import SessionHost
from app.domain.block.comment_threads import CommentThreads
from app.domain.living_doc.schemas import AgentAskIn
from app.domain.topic.services import TopicService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/topics", tags=["doc-agent"])

Chat = Annotated[ChatService, Depends(get_chat_service)]
Sessions = Annotated[SessionHost, Depends(get_session_host)]
Questions = Annotated[Consumptions, Depends(get_consumptions)]


def _sse(event: str, data: dict, position: str | None = None) -> bytes:
    head = f"id: {position}\n" if position is not None else ""
    body = json.dumps(data, ensure_ascii=False)
    return f"{head}event: {event}\ndata: {body}\n\n".encode()


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
    questions: Questions,
):
    """Ask, streamed as server-sent events: ``conversation`` (its id, first),
    ``queued``, ``working``, then once asked ``answering`` (the question's id,
    to read on from elsewhere), ``delta``, ``tool``, then ``done`` (``answer``,
    ``edits``, ``stopped``) or ``error``. Each event from ``answering`` on
    carries its place in the question's stream (``id``)."""
    place, actor, redis = await _asker(db, resolver, topic_id)
    preset = box.PRESETS.get(body.preset or "") if body.preset else None
    if body.preset and preset is None:
        raise ValidationError(say("docAgentPresetNotFound"))
    if preset is not None and (preset.scope == "selection") != (
        body.selection is not None
    ):
        raise ValidationError(say("docAgentPresetNeedsSelection"))
    if body.conversation is not None:
        await box.owned(
            redis, body.conversation, asker=actor.handle, room_id=place.room_id
        )
    conversation = body.conversation or uuid.uuid4()
    may_edit = not await _frozen(db, place)
    selection = (
        box.Selection(body.selection.block, body.selection.start, body.selection.end)
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
            asked = await box.ask(
                chat,
                questions,
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
            if asked is not None:
                async for chunk in _answer(questions, asked.work_id):
                    queue.put_nowait(chunk)
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


@router.get("/{topic_id}/doc/agent/{conversation}/answers/{work}")
async def read_answer(
    topic_id: uuid.UUID,
    conversation: uuid.UUID,
    work: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    questions: Questions,
    after: str = "0",
):
    """The stream of a question asked in the box's conversation, from ``after``
    (the ``id`` of the last event read) to its end, as ``ask_agent`` streams
    it."""
    place, actor, redis = await _asker(db, resolver, topic_id)
    await box.owned(redis, conversation, asker=actor.handle, room_id=place.room_id)
    await db.commit()
    asked = await questions.get(str(work))
    if asked is None or asked.kind != box.KIND or asked.key != str(conversation):
        raise NotFoundError.for_resource("answer", str(work))
    return StreamingResponse(
        _answer(questions, asked.work_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _answer(questions: Consumptions, work: str, after: str = "0"):
    if after == "0":
        yield _sse("answering", {"id": work})
    async for position, event, data in questions.watch(work, after):
        yield _sse(event, data, position)


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
    await box.owned(redis, conversation, asker=actor.handle, room_id=place.room_id)
    await db.commit()
    await box.stop(
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
    held = await box.owned(
        redis, conversation, asker=actor.handle, room_id=place.room_id
    )
    root = await CommentThreads(db).root(place.room_id, thread_id)
    if root.author != actor.handle:
        raise ForbiddenError("Only the thread you started takes this answer")
    if not held.answer:
        raise ValidationError(say("docAgentNoAnswerToComment"))
    bound = await question.bind(db, place.room_id)
    await db.commit()
    await thread.reply(
        chat.session_factory,
        place.room_id,
        place.project_id,
        thread_id,
        bound,
        held.answer,
    )
    return ok({})
