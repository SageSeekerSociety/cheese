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
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.deps import get_handless_sessions
from app.api.response import ok
from app.api.routes.admin_common import DbSession
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.background import spawn
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import (
    AuthenticationRequiredError,
    BaseError,
    ForbiddenError,
    NotFoundError,
    message_key,
)
from app.core.redis import get_redis_client
from app.core.sandbox_auth import personal_claims
from app.core.sentences import say
from app.domain.agent.harness.pi.handless import (
    HandlessSessions,
    HostFull,
    SessionError,
)
from app.domain.agent.harness.pi.personal import Launch
from app.domain.assistant import asking
from app.domain.assistant import service as assistant
from app.domain.assistant import tools as personal_tools
from app.domain.assistant.keys import person_key
from app.domain.assistant.prompt import task_brief
from app.domain.feature_stats import pricing
from app.domain.task.services import TaskService, ensure_task_readable
from app.domain.usage.ledger import Ledger, Rates, payer_for_person

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assistant", tags=["assistant"])

AuthUser = Annotated[AuthUserInfo, Depends(require_auth_user)]
People = Annotated[HandlessSessions, Depends(get_handless_sessions)]


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
                _prestart(people, assistant.launch(auth.user_id, row.id, place)),
                name=f"assistant-prestart-{row.id}",
            )
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


async def _prestart(people: HandlessSessions, started: Launch) -> None:
    try:
        await people.ensure(started)
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
    """Answer one question, streamed as server-sent events: ``delta`` (text),
    ``tool`` (what 芝士 is looking at), ``error``, ``done``."""
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
    if redis is None or not await asking.hold(redis, conversation_id):
        return _refuse(429, say("assistantStillAnswering"), 5)
    held_at = time.monotonic()

    async def release() -> None:
        try:
            await asking.release(redis, conversation_id)
        except Exception:  # noqa: BLE001 — the lock expires by itself
            pass

    started = assistant.launch(auth.user_id, conversation_id, place)
    try:
        await people.ensure(started)
    except HostFull:
        await release()
        return _refuse(503, say("assistantBusy"), 10)
    except SessionError as exc:
        logger.warning("a person's session did not start: %s", exc)
        await release()
        return _refuse(503, assistant.FAILED, 10)

    return StreamingResponse(
        assistant.ask(
            sessions=async_session_factory,
            people=people,
            started=started,
            question=body.question,
            held_at=held_at,
            on_done=release,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/tools/{name}", include_in_schema=False)
async def personal_tool(name: str, request: Request, db: DbSession) -> dict:
    """One tool call of a person's 芝士, run as that person.

    The session presents its personal credential, and nothing else opens this:
    not a person's login, not a room's or a project's credential, not the
    platform's own secret. The conversation it names must still be the person's.
    """
    claims = personal_claims(request.headers.get("x-cheese-token") or "")
    if claims is None:
        raise AuthenticationRequiredError("A personal credential is required")
    try:
        conversation_id = uuid.UUID(claims.conversation_id)
        await assistant.AssistantConversations(db).owned(
            claims.user_id, conversation_id
        )
    except (ValueError, assistant.ConversationNotFound) as exc:
        raise ForbiddenError("This credential's conversation is gone") from exc
    if name not in personal_tools.NAMES:
        raise NotFoundError.for_resource("tool", name)
    try:
        arguments = await request.json()
    except ValueError:
        # What the model wrote is not ours to crash on; the tool reads it as
        # no arguments and says so in its answer.
        arguments = {}
    text = await personal_tools.run(
        name,
        arguments if isinstance(arguments, dict) else {},
        user_id=claims.user_id,
        sessions=async_session_factory,
    )
    return ok({"text": text})
