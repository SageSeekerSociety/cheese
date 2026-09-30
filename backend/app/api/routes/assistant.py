"""A person's 芝士 on a task page: its conversations there, and asking (#2285).

Everything here is the caller's own. A conversation that is someone else's
answers exactly like one that does not exist (404), so an id confirms nothing.
Asking about a task takes the same judgment as reading the task page
(``_ensure_task_readable``): a person who cannot open the task cannot ask about
it either.

A question is admitted only when it can be charged — the model is priced on the
gateway and the person has credits left this month — and only one question at a
time per conversation.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.response import ok
from app.api.routes.admin_common import DbSession
from app.api.routes.tasks import _ensure_task_readable
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import NotFoundError
from app.core.redis import get_redis_client
from app.domain.assistant import agent as assistant_agent
from app.domain.assistant import service as assistant
from app.domain.feature_stats import pricing
from app.domain.service_keys import service_key
from app.domain.task.services import TaskService
from app.domain.usage.personal import PersonalCredits, Rates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assistant", tags=["assistant"])

AuthUser = Annotated[AuthUserInfo, Depends(require_auth_user)]
#: How long one question may hold its conversation before another is let in
#: even without the first one finishing (a crashed worker must not lock it).
BUSY_SECONDS = 180


def _task_place(task_id: int) -> assistant.Place:
    return assistant.Place("task", str(task_id))


async def _readable_task(db, task_id: int, auth: AuthUserInfo):
    task = await TaskService.of(db).get_task(task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    await _ensure_task_readable(db=db, task=task, auth_user=auth)
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
) -> dict:
    row = await _owned(db, auth.user_id, conversation_id)
    messages = await assistant.AssistantConversations(db).messages(row.id)
    return ok(
        {
            **_conversation_out(row),
            "messages": [
                {
                    "role": m.role,
                    "text": m.text,
                    "at": m.created_at.isoformat(),
                }
                for m in messages
            ],
        }
    )


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
    return JSONResponse(
        {"code": status, "message": message}, status_code=status, headers=headers
    )


@router.post("/conversations/{conversationId}/ask")
async def ask(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    body: AskIn,
    db: DbSession,
    auth: AuthUser,
):
    """Answer one question, streamed as server-sent events: ``delta`` (text),
    ``tool`` (what 芝士 is looking at), ``error``, ``done``."""
    row = await _owned(db, auth.user_id, conversation_id)
    place = ""
    if row.place_kind == "task":
        place = assistant_agent.task_brief(
            await _readable_task(db, int(row.place_id), auth)
        )

    rates = Rates.of(settings.assistant_model, await pricing.model_rates())
    key = await service_key(db, assistant_agent.key_spec())
    if rates is None or key is None:
        return _refuse(503, "芝士暂未开放，稍后再试。", 60)
    balance = await PersonalCredits(db).balance(auth.user_id)
    await db.commit()
    if balance.credits_remaining <= 0:
        wait = balance.resets_at - datetime.now(UTC)
        return _refuse(
            429, balance.exhausted_message(), max(60, int(wait.total_seconds()))
        )

    redis = get_redis_client()
    busy = f"assistant:busy:{conversation_id}"
    if redis is None or not await redis.set(busy, "1", nx=True, ex=BUSY_SECONDS):
        return _refuse(429, "上一个问题还在回答，等它答完再问。", 5)

    async def release() -> None:
        try:
            await redis.delete(busy)
        except Exception:  # noqa: BLE001 — the lock expires by itself
            pass

    return StreamingResponse(
        assistant.ask(
            sessions=async_session_factory,
            key=key,
            rates=rates,
            user_id=auth.user_id,
            conversation_id=conversation_id,
            question=body.question,
            place=place,
            on_done=release,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
