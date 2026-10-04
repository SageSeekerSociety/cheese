"""A person's 芝士 on a task page: its conversations there, and asking (#2285).

Everything here is the caller's own. A conversation that is someone else's
answers exactly like one that does not exist (404), so an id confirms nothing.
Asking about a task takes the same judgment as reading the task page
(``_ensure_task_readable``): a person who cannot open the task cannot ask about
it either.

A question is admitted only when it can be charged — the model is priced on the
gateway, the person has a key there, and they have credits left this month —
when the session host is there to answer it, and only one question at a time
per conversation. Its session is started before the answer begins, so a host
that cannot start one is a refusal rather than a broken stream.
"""

import logging
import time
import uuid
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.deps import get_session_host
from app.api.response import ok
from app.api.routes.admin_common import DbSession
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.background import spawn
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import (
    BaseError,
    NotFoundError,
    message_key,
)
from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.agent.personal import service as assistant
from app.domain.agent.personal.keys import person_key
from app.domain.agent.personal.prompt import task_brief
from app.domain.agent.personal.session import session as conversation_session
from app.domain.agent.session_host.contract import (
    Access,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.host import SessionHost
from app.domain.feature_stats import pricing
from app.domain.task.services import TaskService, ensure_task_readable
from app.domain.usage.ledger import Ledger, Rates, payer_for_person

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assistant", tags=["assistant"])

AuthUser = Annotated[AuthUserInfo, Depends(require_auth_user)]
People = Annotated[SessionHost, Depends(get_session_host)]


def _task_place(task_id: int) -> assistant.Place:
    return assistant.Place("task", str(task_id))


async def _readable_task(db, task_id: int, auth: AuthUserInfo):
    task = await TaskService.of(db).get_task(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    await ensure_task_readable(session=db, task=task, user_id=auth.user_id)
    return task


async def _owned(db, user_id: int, conversation_id: uuid.UUID):
    try:
        return await assistant.AssistantConversations(db).owned(
            user_id, conversation_id
        )
    except assistant.ConversationNotFound as exc:
        raise NotFoundError.for_resource("conversation", str(conversation_id)) from exc


def _conversation_out(row, questions: int | None = None) -> dict:
    out = {
        "id": str(row.id),
        "title": row.title,
        "lastActiveAt": row.last_active_at.isoformat(),
    }
    if questions is not None:
        out["questions"] = questions
    return out


@router.get("/tasks/{taskId}/conversations")
async def list_task_conversations(
    task_id: Annotated[int, Path(ge=1, alias="taskId")], db: DbSession, auth: AuthUser
) -> dict:
    """This person's conversations on this task, latest first."""
    await _readable_task(db, task_id, auth)
    store = assistant.AssistantConversations(db)
    rows = await store.list(auth.user_id, _task_place(task_id))
    counts = await store.question_counts([r.id for r in rows])
    return ok(
        {"conversations": [_conversation_out(r, counts.get(r.id, 0)) for r in rows]}
    )


@router.post("/tasks/{taskId}/conversations", status_code=201)
async def start_task_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")], db: DbSession, auth: AuthUser
) -> dict:
    await _readable_task(db, task_id, auth)
    row = await assistant.AssistantConversations(db).start(
        auth.user_id, _task_place(task_id)
    )
    await db.commit()
    return ok(_conversation_out(row, 0))


@router.get("/conversations/{conversationId}")
async def read_conversation(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    db: DbSession,
    auth: AuthUser,
    people: People,
) -> dict:
    """The conversation and what was said in it.

    Opening a conversation is what precedes asking in it, so its session is
    started now, in the background, if it is not running: the first question
    then finds it ready."""
    row = await _owned(db, auth.user_id, conversation_id)
    messages = await assistant.AssistantConversations(db).messages(row.id)
    if people.available():
        try:
            place = await _place(db, row, auth)
        except BaseError:
            place = None
        if place is not None:
            spawn(
                _prestart(people, conversation_session(auth.user_id, row.id, place)),
                name=f"assistant-prestart-{row.id}",
            )
    return ok(
        {
            **_conversation_out(row),
            "messages": [
                {
                    "role": m.role,
                    "text": m.text,
                    "stopped": m.stopped,
                    "at": m.created_at.isoformat(),
                }
                for m in messages
            ],
        }
    )


async def _prestart(
    people: SessionHost, started: tuple[SessionRef, SessionSpec, Access]
) -> None:
    ref, spec, access = started
    try:
        # Only when the host has room now: a question waits for it, and says so.
        await people.start(ref, replace(spec, host_wait_s=0), access)
    except Exception:  # noqa: BLE001 — the question starts it, or says why not
        logger.info("pre-starting a person's session failed", exc_info=True)


async def _place(db, row, auth: AuthUserInfo) -> str:
    """Where the conversation is, written out for 芝士; it takes the same
    judgment as reading that place."""
    if row.place_kind == "task":
        return task_brief(await _readable_task(db, int(row.place_id), auth))
    return ""


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("empty question")
        return v


def _refuse(status: int, message: str, retry_after: int = 0) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    body: dict = {"code": status, "message": message}
    key = message_key(message)
    if key is not None:
        # A catalog sentence: the browser renders it in its reader's language.
        body["error"] = {"message": message, "i18n": key}
    return JSONResponse(body, status_code=status, headers=headers)


@router.post("/conversations/{conversationId}/ask")
async def ask(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    body: AskIn,
    db: DbSession,
    auth: AuthUser,
    people: People,
):
    """Answer one question, streamed as server-sent events: ``queued``,
    ``delta`` (text), ``tool`` (what 芝士 is looking at), ``error``, ``done``
    (``stopped``)."""
    row = await _owned(db, auth.user_id, conversation_id)
    place = await _place(db, row, auth)

    rates = Rates.of(settings.assistant_model, await pricing.model_rates())
    key = await person_key(db, auth.user_id, async_session_factory)
    if rates is None or key is None or not people.available():
        return _refuse(503, say("assistantNotOpen"), 60)
    refused = await Ledger(db).admit(await payer_for_person(db, auth.user_id))
    await db.commit()
    if refused is not None:
        return _refuse(429, refused.message, refused.retry_after_s())

    redis = get_redis_client()
    slot = (
        None
        if redis is None
        else await assistant.take_conversation(redis, conversation_id)
    )
    if redis is None or slot is None:
        return _refuse(429, say("assistantStillAnswering"), 5)
    held_at = time.monotonic()

    return StreamingResponse(
        assistant.ask(
            sessions=async_session_factory,
            redis=redis,
            people=people,
            user_id=auth.user_id,
            conversation_id=conversation_id,
            started=conversation_session(auth.user_id, conversation_id, place),
            question=body.question,
            held_at=held_at,
            on_done=slot.release,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/conversations/{conversationId}/stop")
async def stop(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    db: DbSession,
    auth: AuthUser,
    people: People,
) -> dict:
    """Stop the question being answered: its wait for the session host, or the
    answer being written. What was written is kept, marked as stopped."""
    row = await _owned(db, auth.user_id, conversation_id)
    await db.commit()
    redis = get_redis_client()
    if redis is not None:
        await assistant.stop(redis, people, auth.user_id, row.id)
    return ok({})
