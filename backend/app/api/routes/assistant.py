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
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.deps import get_consumptions, get_session_host
from app.api.refusal import refuse
from app.api.request_deps import AuthUser, DbSession
from app.api.response import ok
from app.api.space_deps import get_space_service
from app.api.task_deps import _task_attachment_service
from app.api.write_access import ROUTE_DECIDES
from app.auth.core import AuthUserInfo
from app.core.background import spawn
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import (
    NotFoundError,
)
from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.agent.personal import billing
from app.domain.agent.personal import service as assistant
from app.domain.agent.personal.keys import person_key
from app.domain.agent.personal.prompt import task_brief
from app.domain.agent.personal.session import session as conversation_session
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.agent.session_host.contract import (
    Access,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.host import SessionHost
from app.domain.space.services import SpaceService
from app.domain.task import drafts
from app.domain.task.claims import claim_of
from app.domain.task.services import TaskService, ensure_task_readable
from app.domain.usage import pricing
from app.domain.usage.ledger import Ledger, Rates, payer_for_person
from app.domain.user.services import timezones_by_ids

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assistant", tags=["assistant"])


People = Annotated[SessionHost, Depends(get_session_host)]
Questions = Annotated[Consumptions, Depends(get_consumptions)]
Spaces = Annotated[SpaceService, Depends(get_space_service)]


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


@router.post(
    "/tasks/{taskId}/conversations", status_code=201, dependencies=[ROUTE_DECIDES]
)
async def start_task_conversation(
    task_id: Annotated[int, Path(ge=1, alias="taskId")], db: DbSession, auth: AuthUser
) -> dict:
    await _readable_task(db, task_id, auth)
    row = await assistant.AssistantConversations(db).start(
        auth.user_id, _task_place(task_id)
    )
    await db.commit()
    return ok(_conversation_out(row, 0))


@router.post(
    "/drafts/{draftId}/conversations", status_code=201, dependencies=[ROUTE_DECIDES]
)
async def start_draft_conversation(
    draft_id: Annotated[int, Path(ge=1, alias="draftId")], db: DbSession, auth: AuthUser
) -> dict:
    """A conversation on a draft's page. The draft remembers it, so the page
    opens on it again, and when the draft is published the conversation goes
    with the 题目."""
    row = await drafts.mine(db, auth.user_id, draft_id)
    conversation = await assistant.AssistantConversations(db).start(
        auth.user_id, assistant.Place("draft", str(draft_id))
    )
    row.conversation_id = conversation.id
    await db.commit()
    return ok(_conversation_out(conversation, 0))


@router.get("/conversations/{conversationId}")
async def read_conversation(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    db: DbSession,
    auth: AuthUser,
    people: People,
    questions: Questions,
) -> dict:
    """The conversation and what was said in it, and the question being
    answered in it now, if one is (``answering``: read its stream to see the
    answer as it is written).

    Opening a conversation is what precedes asking in it, so its session is
    started now, in the background, if it is not running: the first question
    then finds it ready."""
    row = await _owned(db, auth.user_id, conversation_id)
    messages = await assistant.AssistantConversations(db).messages(row.id)
    if people.available():
        spawn(
            _prestart(people, conversation_session(auth.user_id, row.id)),
            name=f"assistant-prestart-{row.id}",
        )
    answering = (
        await questions.current(assistant.KIND, str(row.id))
        if get_redis_client() is not None
        else None
    )
    return ok(
        {
            **_conversation_out(row),
            "answering": None if answering is None else answering.work_id,
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
    except Exception:  # the question starts it, or says why not
        logger.info("pre-starting a person's session failed", exc_info=True)


class PageIn(BaseModel):
    """The page a question is asked from: a 题目's, or a draft's (the publish
    or edit page)."""

    kind: Literal["task", "draft"]
    id: int = Field(ge=1)


async def _page(db, spaces: SpaceService, page: PageIn, auth: AuthUserInfo) -> str:
    """The page, written out for 芝士; it takes the same judgment as opening
    the page."""
    if page.kind == "draft":
        row = await drafts.mine(db, auth.user_id, page.id)
        zones = await timezones_by_ids(db, [auth.user_id])
        return await drafts.context(db, spaces, row, zones.get(auth.user_id))
    task = await _readable_task(db, page.id, auth)
    zones = await timezones_by_ids(db, [auth.user_id])
    files, _, readable = await _task_attachment_service(db).list_for_task(
        task=task, user_id=auth.user_id
    )
    return task_brief(
        task,
        claim=await claim_of(db, task=task, user_id=auth.user_id),
        timezone=zones.get(auth.user_id),
        materials=[(f.id, (f.meta or {}).get("filename") or str(f.id)) for f in files],
        materials_readable=readable,
    )


def _page_of(row) -> PageIn | None:
    """Where a conversation was started, as the page a question defaults to."""
    if row.place_kind in ("task", "draft") and row.place_id.isdigit():
        return PageIn(kind=row.place_kind, id=int(row.place_id))
    return None


class FileIn(BaseModel):
    id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    #: The page it is asked from; the conversation's own place when left out.
    page: PageIn | None = None
    #: Files handed over with it (uploaded with ``POST /attachments``); 芝士
    #: reads them with ``cheese_file_read``, as the asker may.
    files: list[FileIn] = Field(default_factory=list, max_length=10)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("empty question")
        return v


@router.post("/conversations/{conversationId}/ask", dependencies=[ROUTE_DECIDES])
async def ask(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    body: AskIn,
    db: DbSession,
    auth: AuthUser,
    people: People,
    questions: Questions,
    spaces: Spaces,
):
    """Answer one question, streamed as server-sent events: ``answering`` (the
    question's id, to read on from elsewhere), then ``queued`` (waiting for the
    session host), ``delta`` (text), ``tool`` (what 芝士 is looking at),
    ``error``, ``done`` (``stopped``). Each event carries its place in the
    question's stream (``id``)."""
    row = await _owned(db, auth.user_id, conversation_id)
    shown = body.page or _page_of(row)
    context = "" if shown is None else await _page(db, spaces, shown, auth)

    rates = Rates.of(settings.assistant_model, await pricing.model_rates())
    key = await person_key(db, auth.user_id, async_session_factory)
    if rates is None or key is None or not people.available():
        return refuse(503, say("assistantNotOpen"), 60)
    refused = await Ledger(db).admit(await payer_for_person(db, auth.user_id))
    await db.commit()
    if refused is not None:
        return refuse(429, refused.message, refused.retry_after_s())

    redis = get_redis_client()
    slot = (
        None
        if redis is None
        else await assistant.take_conversation(redis, conversation_id)
    )
    if redis is None or slot is None:
        return refuse(429, say("assistantStillAnswering"), 5)
    held_at = time.monotonic()
    said = await assistant.page_to_say(redis, conversation_id, context)

    try:
        asked = await assistant.ask(
            sessions=async_session_factory,
            consumptions=questions,
            user_id=auth.user_id,
            conversation_id=conversation_id,
            started=conversation_session(auth.user_id, conversation_id),
            question=body.question,
            held_at=held_at,
            slot=slot,
            context=said,
            files=[(f.id, f.name) for f in body.files],
            kind=(
                billing.WRITING
                if shown is not None and shown.kind == "draft"
                else billing.ASKING
            ),
        )
    except BaseException:
        await slot.release()
        raise
    return _stream(questions, asked.work_id)


@router.get("/conversations/{conversationId}/answers/{work}")
async def read_answer(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    work: uuid.UUID,
    db: DbSession,
    auth: AuthUser,
    questions: Questions,
    after: str = "0",
):
    """The stream of a question asked in this conversation, from ``after`` (the
    ``id`` of the last event read) to its end, as ``ask`` streams it."""
    row = await _owned(db, auth.user_id, conversation_id)
    await db.commit()
    asked = await questions.get(str(work))
    if asked is None or asked.kind != assistant.KIND or asked.key != str(row.id):
        raise NotFoundError.for_resource("answer", str(work))
    return _stream(questions, asked.work_id, after)


def _stream(questions: Consumptions, work: str, after: str = "0") -> StreamingResponse:
    async def events():
        if after == "0":
            yield assistant.sse("answering", {"id": work})
        async for position, event, data in questions.watch(work, after):
            yield assistant.sse(event, data, position)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/conversations/{conversationId}/stop", dependencies=[ROUTE_DECIDES])
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
