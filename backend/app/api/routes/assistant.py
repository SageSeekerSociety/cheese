"""A person's 芝士: their thread with it, and asking (#2285).

A person has one thread with their 芝士, wherever they open it; each
conversation is a topic in it, and 「新话题」 starts the next. The page a
question is asked from goes with the question (``pages``), so the thread
follows the person across pages.

Everything here is the caller's own. A conversation that is someone else's
answers exactly like one that does not exist (404), so an id confirms nothing.
Asking from a page takes the same judgment as opening that page: a person who
cannot open a task cannot ask about it either.

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
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.assistant_deps import ProjectPages, not_by_their_assistant
from app.api.auth import ActorResolverDep
from app.api.deps import get_consumptions, get_session_host
from app.api.refusal import refuse
from app.api.request_deps import AuthUser, DbSession
from app.api.response import ok, typed_response
from app.api.space_deps import get_space_service
from app.api.write_access import ROUTE_DECIDES
from app.auth.core import AuthUserInfo
from app.core.background import spawn
from app.core.config import settings
from app.core.db import async_session_factory
from app.core.errors import (
    BadRequestError,
    BaseError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.redis import get_redis_client
from app.core.sentences import say
from app.domain.agent.personal import citing, pages
from app.domain.agent.personal import service as assistant
from app.domain.agent.personal.session import session as conversation_session
from app.domain.agent.session_host.consumptions import Consumptions
from app.domain.agent.session_host.contract import (
    Access,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.host import SessionHost
from app.domain.assistant import changes, gate, proposals
from app.domain.assistant import memory as assistant_memory
from app.domain.memory.files import MemoryFileError
from app.domain.project.services import ProjectService
from app.domain.routine import personal as routine_personal
from app.domain.space.services import SpaceService
from app.domain.team.services import team_service
from app.domain.usage.credits import CREDIT_USD
from app.domain.user.services import timezones_by_ids, usernames_by_ids
from app.domain.voice.service import VoiceInputs

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/assistant",
    tags=["assistant"],
    dependencies=[Depends(not_by_their_assistant)],
)


People = Annotated[SessionHost, Depends(get_session_host)]
Questions = Annotated[Consumptions, Depends(get_consumptions)]
Spaces = Annotated[SpaceService, Depends(get_space_service)]


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
        # A routine's run: started by the platform, not by the person.
        "routine": row.place_kind == "routine",
    }
    if questions is not None:
        out["questions"] = questions
    return out


class ConversationOut(BaseModel):
    """A topic as the panel lists it."""

    id: str
    title: str
    lastActiveAt: str
    routine: bool = False
    questions: int | None = None


class ConversationsOut(BaseModel):
    """A page of the person's topics, latest first, and where the next page
    starts (``next``; none when there are no older topics)."""

    conversations: list[ConversationOut]
    next: str | None = None


class CitableOut(BaseModel):
    """One thing the person may cite with @: its id as the token carries it,
    its kind (``channel``, ``task``, ``doc``, ``file``), its name, where it
    lives (a project, a team, the person's own library), and a task's
    channel."""

    id: str
    kind: str
    title: str
    where: str
    place: str | None = None


class CitablesOut(BaseModel):
    items: list[CitableOut]


@router.get("/citable", **typed_response(CitablesOut))
async def citable(
    db: DbSession,
    auth: AuthUser,
    resolver: ActorResolverDep,
    project: uuid.UUID | None = None,
) -> dict:
    """What the person may cite with @ in a question, for the panel to match
    as they type. ``project`` is the one they are in: its library's files are
    listed too. Only what they may open is listed."""
    me = await resolver.resolve()
    access = ProjectPages(db, resolver)
    projects = await ProjectService(db).list_visible_to(
        handle=me.handle, user_id=auth.user_id
    )
    channels: dict[uuid.UUID, list] = {}
    names: dict[uuid.UUID, str] = {}
    for one in projects:
        if one.archived_at is not None:
            continue
        try:
            channels[one.id] = await access.channels(one.id)
        except BaseError:  # listed, but not theirs to open now
            continue
        names[one.id] = one.name
    teams = await team_service(db).get_teams_of_user(auth.user_id)
    own = await team_service(db).ensure_personal_team(auth.user_id)
    items = await citing.citable(
        db,
        channels=channels,
        project_names=names,
        teams=[*teams, *([] if any(t.id == own.id for t in teams) else [own])],
        own_team_id=own.id,
        here=project,
    )
    return ok({"items": items})


def _topic_cursor(row) -> str:
    return f"{row.last_active_at.isoformat()}|{row.id}"


def _read_topic_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        at, _, of = cursor.partition("|")
        return datetime.fromisoformat(at), uuid.UUID(of)
    except ValueError as error:
        raise BadRequestError(say("listCursorUnreadable")) from error


@router.get("/conversations", **typed_response(ConversationsOut))
async def list_conversations(
    db: DbSession,
    auth: AuthUser,
    before: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = assistant.TOPICS_PAGE,
) -> dict:
    """This person's topics with 芝士, latest first: the thread the panel shows,
    wherever it is opened. A page at a time: ``before`` is the ``next`` of the
    page read before."""
    store = assistant.AssistantConversations(db)
    rows, more = await store.list(
        auth.user_id,
        before=None if before is None else _read_topic_cursor(before),
        limit=limit,
    )
    counts = await store.question_counts([r.id for r in rows])
    return ok(
        {
            "conversations": [_conversation_out(r, counts.get(r.id, 0)) for r in rows],
            "next": _topic_cursor(rows[-1]) if more and rows else None,
        }
    )


@router.post(
    "/conversations",
    status_code=201,
    dependencies=[ROUTE_DECIDES],
    **typed_response(ConversationOut),
)
async def start_conversation(db: DbSession, auth: AuthUser) -> dict:
    """A new topic: what was said before it is no longer read with the next
    question. It is listed once a question is asked in it."""
    row = await assistant.AssistantConversations(db).start(
        auth.user_id, assistant.Place.personal(auth.user_id)
    )
    await db.commit()
    return ok(_conversation_out(row, 0))


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
    messages, earlier = await assistant.AssistantConversations(db).latest(row.id)
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
            "messages": await _messages_out(db, auth.user_id, messages),
            "earlier": earlier,
        }
    )


class EarlierOut(BaseModel):
    """What was said in a conversation before a message, in order, and
    whether there is more before it."""

    messages: list[dict[str, Any]]
    earlier: bool


@router.get("/conversations/{conversationId}/messages", **typed_response(EarlierOut))
async def read_earlier(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    db: DbSession,
    auth: AuthUser,
    before: Annotated[int, Query(ge=1)],
    limit: Annotated[int, Query(ge=1, le=100)] = assistant.MESSAGES_PAGE,
) -> dict:
    """What was said in a conversation before message ``before`` (its
    ``seq``), the last ``limit`` of it in order, and whether there is more
    before that (``earlier``): the panel reads back as the person scrolls up."""
    row = await _owned(db, auth.user_id, conversation_id)
    messages, earlier = await assistant.AssistantConversations(db).latest(
        row.id, before=before, limit=limit
    )
    return ok(
        {
            "messages": await _messages_out(db, auth.user_id, messages),
            "earlier": earlier,
        }
    )


async def _messages_out(db, user_id: int, messages) -> list[dict]:
    """Messages as the panel shows them, each kept answer's parts as they
    stand now."""
    undone = await changes.undone_of(
        db,
        user_id,
        [
            e["id"]
            for m in messages
            for e in (m.parts or {}).get("edits") or []
            if isinstance(e.get("id"), int)
        ],
    )
    drafted = await proposals.now_of(
        db,
        user_id,
        [
            d["id"]
            for m in messages
            for d in (m.parts or {}).get("proposals") or []
            if isinstance(d.get("id"), int)
        ],
    )
    return [
        {
            "seq": m.seq,
            "role": m.role,
            "text": m.text,
            "stopped": m.stopped,
            "at": m.created_at.isoformat(),
            **({"parts": _now(m.parts, undone, drafted)} if m.parts else {}),
        }
        for m in messages
    ]


def _now(
    parts: dict,
    undone: dict[int, list],
    drafted: dict[int, tuple[str, dict | None]],
) -> dict:
    """A kept answer's parts as they stand now: what is undone of its changes,
    and whether what it drafted was sent."""
    out = dict(parts)
    if parts.get("edits"):
        out["edits"] = [
            {**e, "undone": undone[e["id"]]} if e.get("id") in undone else e
            for e in parts["edits"]
        ]
    if parts.get("proposals"):
        out["proposals"] = [
            {
                **d,
                "state": drafted[d["id"]][0],
                **({"result": drafted[d["id"]][1]} if drafted[d["id"]][1] else {}),
            }
            if d.get("id") in drafted
            else d
            for d in parts["proposals"]
        ]
    return out


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
    """The page a question is asked from: which kind of page, and which one
    (a number, or a project's page's UUID). The kinds are the pages 芝士 knows
    how to read (``pages.PAGES``)."""

    kind: pages.PageKind
    id: str = Field(min_length=1, max_length=64)

    @field_validator("id", mode="before")
    @classmethod
    def _text(cls, v):
        return str(v)


async def _reader(
    db, spaces: SpaceService, auth: AuthUserInfo, resolver
) -> pages.Reader:
    zones = await timezones_by_ids(db, [auth.user_id])
    return pages.Reader(
        session=db,
        user_id=auth.user_id,
        timezone=zones.get(auth.user_id),
        spaces=spaces,
        access=ProjectPages(db, resolver),
    )


async def _page(
    db, spaces: SpaceService, page: PageIn, auth: AuthUserInfo, resolver
) -> pages.Shown:
    """The page, written out for 芝士; it takes the same judgment as opening
    the page."""
    reader = await _reader(db, spaces, auth, resolver)
    return await pages.render(reader, page.kind, page.id)


def _page_of(row) -> PageIn | None:
    """Where a conversation was started, as the page a question defaults to."""
    home = pages.home_page(row.place_kind, row.place_id)
    return None if home is None else PageIn(kind=home[0], id=home[1])


class FileIn(BaseModel):
    """A file handed over with a question: one kept in the person's own
    library (``path``), or, on a page whose files are a task's materials, the
    attachment it was uploaded as (``id``)."""

    id: int | None = Field(default=None, ge=1)
    path: str | None = Field(default=None, min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def _one(self) -> "FileIn":
        if (self.id is None) == (self.path is None):
            raise ValueError(say("assistantFileOneOf"))
        return self

    @property
    def ref(self) -> str:
        """How 芝士 reads it (``cheese_read``)."""
        return f"library/{self.path}" if self.path is not None else f"files/{self.id}"


class AskIn(BaseModel):
    #: Empty when files are handed over alone: what they are for is then
    #: what the page says a file there means.
    question: str = Field(default="", max_length=4000)
    #: The page it is asked from; the conversation's own place when left out.
    page: PageIn | None = None
    #: Asked from no page: the person took the page off, or is on one 芝士
    #: does not read. Nothing stands in for it, not even the conversation's
    #: own place.
    no_page: bool = False
    #: Text the person selected on the page and asked about.
    selection: str | None = Field(default=None, max_length=citing.SELECTION_CHARS)
    #: Files handed over with it (uploaded with ``POST /attachments``); 芝士
    #: reads them with ``cheese_read`` (``files/<id>``), as the asker may.
    files: list[FileIn] = Field(default_factory=list, max_length=10)
    #: Already in the conversation: said while an answer was going
    #: (``say``), which ended before 芝士 heard it, and now asked on its own.
    kept: bool = False

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _something(self) -> "AskIn":
        if not self.question and not self.files:
            raise ValueError("empty question")
        if len(citing.tokens(self.question)) > citing.MOST:
            raise ValueError(say("assistantCitesTooMany", most=citing.MOST))
        return self


@router.post("/conversations/{conversationId}/ask", dependencies=[ROUTE_DECIDES])
async def ask(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    body: AskIn,
    db: DbSession,
    auth: AuthUser,
    people: People,
    questions: Questions,
    spaces: Spaces,
    resolver: ActorResolverDep,
):
    """Answer one question, streamed as server-sent events: ``answering`` (the
    question's id, to read on from elsewhere), then ``queued`` (waiting for the
    session host), ``delta`` (text), ``tool`` (what 芝士 is looking at),
    ``error``, ``done`` (``stopped``). Each event carries its place in the
    question's stream (``id``)."""
    row = await _owned(db, auth.user_id, conversation_id)
    shown = None if body.no_page else body.page or _page_of(row)
    page = None if shown is None else await _page(db, spaces, shown, auth, resolver)
    context = pages.NO_PAGE if body.no_page else "" if page is None else page.text
    cited = await citing.cite(
        await _reader(db, spaces, auth, resolver), citing.tokens(body.question)
    )
    asked_with = {
        **({"page": page.label} if page is not None and page.label else {}),
        **({"selection": body.selection} if body.selection else {}),
        **({"cited": cited.kept} if cited.kept else {}),
    }

    redis = get_redis_client()
    slot = await assistant.admit(
        db,
        redis,
        people,
        user_id=auth.user_id,
        conversation_id=conversation_id,
        sessions=async_session_factory,
    )
    if isinstance(slot, assistant.Refused):
        return refuse(slot.status, slot.message, slot.retry_after_s)
    assert redis is not None
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
            told=citing.in_words(body.question, cited.named),
            cited="\n\n".join(
                part
                for part in (citing.selection(body.selection), cited.text())
                if part
            ),
            asked_with=asked_with,
            held_at=held_at,
            slot=slot,
            context=said,
            files=[(f.ref, f.name) for f in body.files],
            kind=pages.billed_as(None if shown is None else shown.kind),
            project=None if page is None else page.project,
            kept=body.kept,
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


class SayIn(BaseModel):
    #: Empty when files are handed over alone.
    text: str = Field(default="", max_length=4000)
    #: Files handed over with it, as with a question.
    files: list[FileIn] = Field(default_factory=list, max_length=10)
    #: Text selected on the page, as with a question.
    selection: str | None = Field(default=None, max_length=citing.SELECTION_CHARS)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class SaidOut(BaseModel):
    said: bool


@router.post(
    "/conversations/{conversationId}/say",
    dependencies=[ROUTE_DECIDES],
    **typed_response(SaidOut),
)
async def say_in(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    body: SayIn,
    db: DbSession,
    auth: AuthUser,
    questions: Questions,
    spaces: Spaces,
    resolver: ActorResolverDep,
) -> dict:
    """Something the person says while the conversation's question is being
    answered: kept in the conversation as theirs, and said to 芝士 in the
    middle of its answer, which it reads before its next step (a long piece of
    work need not be stopped to be steered). Nothing being answered, it is
    refused: ask it as a question.

    The answer may be read by another process, or by none for a moment (a
    deploy hands it over): what is said waits with the question for whoever
    reads it (``Consumptions.say``). If the answer ends first, its stream
    gives it back (``unheard``), to be asked again with ``kept``."""
    row = await _owned(db, auth.user_id, conversation_id)
    answering = (
        await questions.current(assistant.KIND, str(row.id))
        if get_redis_client() is not None
        else None
    )
    if answering is None or not (body.text or body.files):
        raise ConflictError(say("assistantNotAnswering"))
    files = [(f.ref, f.name) for f in body.files]
    cited = await citing.cite(
        await _reader(db, spaces, auth, resolver), citing.tokens(body.text)
    )
    await assistant.AssistantConversations(db).append(
        row,
        "user",
        body.text,
        parts={
            **({"files": [name for _, name in files]} if files else {}),
            **({"selection": body.selection} if body.selection else {}),
            **({"cited": cited.kept} if cited.kept else {}),
        }
        or None,
    )
    await db.commit()
    told = "\n\n".join(
        part
        for part in (
            citing.in_words(body.text, cited.named),
            citing.selection(body.selection),
            cited.text(),
        )
        if part
    )
    await questions.say(
        answering,
        told + assistant.files_said(files),
        body.model_dump(exclude_none=True),
    )
    return ok({"said": True})


#: A full-length recording at 16 kHz, 16-bit mono is 1.92 MB; anything far
#: past that is not one the composer made.
_VOICE_BYTES = 4 * 1024 * 1024


class HeardOut(BaseModel):
    text: str
    raw: str
    seconds: float
    remaining_seconds: int


@router.post("/voice", dependencies=[ROUTE_DECIDES], **typed_response(HeardOut))
async def hear_voice(
    db: DbSession,
    auth: AuthUser,
    file: UploadFile = File(...),
) -> dict:
    """Hear a WAV recording said to the person's own 芝士, as the composer in a
    channel does: ``text`` tidied, ``raw`` word for word. It is in no project,
    so the person pays for it, out of the same daily allowance."""
    audio = await file.read(_VOICE_BYTES + 1)
    if len(audio) > _VOICE_BYTES:
        raise ValidationError(say("voiceTooLong", seconds=settings.voice_max_seconds))
    heard = await VoiceInputs(db).hear(
        user_id=auth.user_id, project_id=None, topic_id=None, audio=audio, names=[]
    )
    return ok(
        {
            "text": heard.text,
            "raw": heard.raw,
            "seconds": round(heard.seconds, 1),
            "remaining_seconds": round(heard.remaining_seconds),
        }
    )


class LookingOut(BaseModel):
    """How long the word holds before the panel says it again."""

    seconds: int


@router.post(
    "/conversations/{conversationId}/looking",
    dependencies=[ROUTE_DECIDES],
    **typed_response(LookingOut),
)
async def look(
    conversation_id: Annotated[uuid.UUID, Path(alias="conversationId")],
    db: DbSession,
    auth: AuthUser,
) -> dict:
    """The panel is showing this conversation to its person now: an answer
    that ends while it does is not also told as a notification. Said again
    every few seconds while it is (``LOOKING_S``)."""
    row = await _owned(db, auth.user_id, conversation_id)
    await db.commit()
    redis = get_redis_client()
    if redis is not None:
        await assistant.looking(redis, row.id)
    return ok({"seconds": assistant.LOOKING_S})


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


class UndoIn(BaseModel):
    #: One part of the change (a draft's field as 芝士 names it); all of it
    #: when left out.
    part: str | None = None


class UndoneOut(BaseModel):
    """What undoing (or redoing) did: every change of the answer it touched,
    as it is now, and what the kind of change says about it — a draft's
    fields put back and its version, or the document written back."""

    changes: list[dict]
    draft: int | None = None
    version: int | None = None
    fields: dict | None = None
    document: str | None = None


@router.post(
    "/changes/{changeId}/undo",
    dependencies=[ROUTE_DECIDES],
    **typed_response(UndoneOut),
)
async def undo_change(
    change_id: Annotated[int, Path(alias="changeId", ge=1)],
    body: UndoIn,
    db: DbSession,
    auth: AuthUser,
    resolver: ActorResolverDep,
) -> dict:
    """Undo what 芝士 changed while answering, as the person: a draft's field
    back to what it was before the answer, a document's passages back to what
    they were."""
    return ok(
        await changes.undo(
            db, resolver, user_id=auth.user_id, change_id=change_id, part=body.part
        )
    )


@router.post(
    "/changes/{changeId}/redo",
    dependencies=[ROUTE_DECIDES],
    **typed_response(UndoneOut),
)
async def redo_change(
    change_id: Annotated[int, Path(alias="changeId", ge=1)],
    body: UndoIn,
    db: DbSession,
    auth: AuthUser,
    resolver: ActorResolverDep,
) -> dict:
    """Put back what was undone of a change: as 芝士 left it."""
    return ok(
        await changes.undo(
            db,
            resolver,
            user_id=auth.user_id,
            change_id=change_id,
            part=body.part,
            redo=True,
        )
    )


class SettleIn(BaseModel):
    #: ``sent`` once the person sent it, with what was made; ``dismissed``
    #: when they put it aside.
    state: Literal["sent", "dismissed"]
    result: dict | None = None


class ProposalOut(BaseModel):
    id: int
    kind: str
    state: str
    result: dict | None = None


@router.post(
    "/proposals/{proposalId}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(ProposalOut),
)
async def settle_proposal(
    proposal_id: Annotated[int, Path(alias="proposalId", ge=1)],
    body: SettleIn,
    db: DbSession,
    auth: AuthUser,
) -> dict:
    """The person sent what 芝士 drafted (through the routes they would use by
    hand), or put it aside."""
    row = await proposals.settle(
        db,
        user_id=auth.user_id,
        proposal_id=proposal_id,
        state=body.state,
        result=body.result,
    )
    return ok(
        {"id": row.id, "kind": row.kind, "state": row.state, "result": row.result}
    )


# What a person tells their 芝士 about acting for them, and what it did: the
# person's own, on their settings page. Not opened to 芝士's credential, so it
# cannot change its own rules.


#: The most entries a person keeps in either list, and the longest one.
RULE_ENTRIES = 50
RULE_CHARS = 200


class RulesIn(BaseModel):
    #: Kinds of action always asked about (``gate.CATEGORIES``).
    ask: list[str] = Field(default_factory=list, max_length=len(gate.CATEGORIES))
    #: The person's own entries: what needs confirming, and what does not.
    askItems: list[Annotated[str, Field(max_length=RULE_CHARS)]] = Field(
        default_factory=list, max_length=RULE_ENTRIES
    )
    allowItems: list[Annotated[str, Field(max_length=RULE_CHARS)]] = Field(
        default_factory=list, max_length=RULE_ENTRIES
    )


class RuleCategoryOut(BaseModel):
    code: str
    label: str


class RulesOut(BaseModel):
    """What the person told their 芝士: kinds always asked, and their own
    entries of what needs confirming and what does not."""

    ask: list[str]
    askItems: list[str]
    allowItems: list[str]
    categories: list[RuleCategoryOut]


class ActivityItemOut(BaseModel):
    id: int
    kind: str
    summary: str
    verdict: Literal["allowed", "asked"]
    stage: str
    reason: str
    at: str


class ActivityOut(BaseModel):
    items: list[ActivityItemOut]


def _rules_out(row) -> dict:
    return {
        "ask": [c for c in row.ask if c in gate.CATEGORIES],
        "askItems": gate.entries(row.ask_text),
        "allowItems": gate.entries(row.allow_text),
        "categories": [{"code": c, "label": t} for c, t in gate.CATEGORIES.items()],
    }


def _lines(items: list[str]) -> str:
    """The person's entries as kept: one a line, each said once."""
    kept = dict.fromkeys(" ".join(i.split()) for i in items if i.strip())
    return "\n".join(kept)


@router.get("/rules", **typed_response(RulesOut))
async def get_rules(db: DbSession, auth: AuthUser) -> dict:
    """What the person told their 芝士 to ask first and may simply do."""
    return ok(_rules_out(await gate.rules_of(db, auth.user_id)))


@router.put("/rules", dependencies=[ROUTE_DECIDES], **typed_response(RulesOut))
async def put_rules(body: RulesIn, db: DbSession, auth: AuthUser) -> dict:
    unknown = [c for c in body.ask if c not in gate.CATEGORIES]
    if unknown:
        raise BadRequestError(say("assistantRuleUnknown", rule=unknown[0]))
    row = await gate.save_rules(
        db,
        auth.user_id,
        ask=list(dict.fromkeys(body.ask)),
        allow_text=_lines(body.allowItems),
        ask_text=_lines(body.askItems),
    )
    await db.commit()
    return ok(_rules_out(row))


class MemoryItemOut(BaseModel):
    name: str
    description: str
    type: str
    text: str
    updatedAt: str


class MemoryOut(BaseModel):
    """What the person's 芝士 remembers of them, and whether it may."""

    enabled: bool
    items: list[MemoryItemOut]


class MemoryEnabledIn(BaseModel):
    enabled: bool


class MemoryItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=150)
    text: str = Field(min_length=1, max_length=1000)


async def _handle(db, user_id: int) -> str:
    handle = (await usernames_by_ids(db, [user_id])).get(user_id)
    if not handle:
        raise NotFoundError.for_resource("user", str(user_id))
    return handle


async def _memory_out(db, user_id: int) -> dict:
    handle = await _handle(db, user_id)
    rows = await assistant_memory.entries(db, handle)
    return {
        "enabled": (await gate.rules_of(db, user_id)).memory,
        "items": [
            {
                "name": e.memory.name,
                "description": e.memory.description,
                "type": e.memory.type.value,
                "text": e.memory.body,
                "updatedAt": e.updated_at,
            }
            for e in rows
        ],
    }


@router.get("/memory", **typed_response(MemoryOut))
async def get_memory(db: DbSession, auth: AuthUser) -> dict:
    """What the person's 芝士 remembers of them. Theirs alone: 芝士's own
    credential does not reach it, so it cannot change what it was told."""
    return ok(await _memory_out(db, auth.user_id))


@router.put("/memory", dependencies=[ROUTE_DECIDES], **typed_response(MemoryOut))
async def put_memory_enabled(
    body: MemoryEnabledIn, db: DbSession, auth: AuthUser
) -> dict:
    """Memory on or off. Off keeps what is remembered, neither used nor added
    to."""
    await gate.set_memory(db, auth.user_id, body.enabled)
    await db.commit()
    return ok(await _memory_out(db, auth.user_id))


@router.put("/memory/{name}", dependencies=[ROUTE_DECIDES], **typed_response(MemoryOut))
async def put_memory_item(
    name: Annotated[str, Path()], body: MemoryItemIn, db: DbSession, auth: AuthUser
) -> dict:
    """The person rewrites one memory: what it says and what it is for."""
    handle = await _handle(db, auth.user_id)
    found = await assistant_memory.read(db, handle, name)
    if found is None:
        raise NotFoundError.for_resource("memory", name)
    try:
        memory = assistant_memory.made(
            name, body.description, found.type.value, body.text
        )
    except MemoryFileError as exc:
        raise BadRequestError(str(exc)) from exc
    await assistant_memory.save(db, handle, memory, by=handle)
    await db.commit()
    return ok(await _memory_out(db, auth.user_id))


@router.delete(
    "/memory/{name}", dependencies=[ROUTE_DECIDES], **typed_response(MemoryOut)
)
async def delete_memory_item(
    name: Annotated[str, Path()], db: DbSession, auth: AuthUser
) -> dict:
    handle = await _handle(db, auth.user_id)
    before = await assistant_memory.put(
        db, handle, assistant_memory.path_of(name), None, by=handle
    )
    if before is None:
        raise NotFoundError.for_resource("memory", name)
    await db.commit()
    return ok(await _memory_out(db, auth.user_id))


@router.delete("/memory", dependencies=[ROUTE_DECIDES], **typed_response(MemoryOut))
async def clear_memory(db: DbSession, auth: AuthUser) -> dict:
    """Forget everything the person's 芝士 remembers of them."""
    await assistant_memory.clear(db, await _handle(db, auth.user_id))
    await db.commit()
    return ok(await _memory_out(db, auth.user_id))


@router.get("/activity", **typed_response(ActivityOut))
async def get_activity(
    db: DbSession,
    auth: AuthUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict:
    """What 芝士 did for the person, or put to them to confirm, newest first,
    and why."""
    rows = await gate.recent(db, auth.user_id, limit=limit)
    return ok(
        {
            "items": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "summary": r.summary,
                    "verdict": r.verdict,
                    "stage": r.stage,
                    "reason": r.reason,
                    "at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        }
    )


# A person's routines, as their settings page lists them: what 芝士 does on its
# own for them, how its last run went, and what it cost this week.


class RoutineRunOut(BaseModel):
    status: str
    at: str
    asked: int
    conversationId: str | None = None


class RoutineOut(BaseModel):
    id: str
    title: str
    note: str
    #: When it runs, in a line.
    when: str
    #: ``done``: a one-off that has run; it is cleared once its cards expire.
    state: Literal["active", "paused", "done"]
    #: Why a paused one stopped: ``person``, ``credits`` or ``away``.
    paused: str
    nextRunAt: str | None = None
    #: Fires once, and runs in the conversation it was asked for in.
    once: bool
    #: The conversation it was asked for in.
    conversationId: str | None = None
    lastRun: RoutineRunOut | None = None
    #: What its runs cost over the last seven days, in credits.
    weekCredits: float


class RoutinesOut(BaseModel):
    routines: list[RoutineOut]
    limit: int


async def _routines_out(db, user_id: int) -> dict:
    rows = await routine_personal.mine(db, user_id)
    ids = [r.id for r in rows]
    last = await routine_personal.last_runs(db, ids)
    costs = await routine_personal.week_costs(db, ids)
    return {
        "limit": routine_personal.MAX_ROUTINES,
        "routines": [
            {
                "id": str(r.id),
                "title": r.title,
                "note": r.note,
                "when": routine_personal.describe(r),
                "state": r.state,
                "paused": r.paused,
                "nextRunAt": r.next_run_at.isoformat()
                if r.next_run_at and r.state == "active"
                else None,
                "once": routine_personal.once(r),
                "conversationId": str(r.conversation_id) if r.conversation_id else None,
                "lastRun": None
                if r.id not in last
                else {
                    "status": last[r.id].status,
                    "at": (last[r.id].finished_at or last[r.id].created_at).isoformat(),
                    "asked": last[r.id].asked,
                    "conversationId": str(last[r.id].conversation_id)
                    if last[r.id].conversation_id
                    else None,
                },
                "weekCredits": round(costs.get(r.id, 0.0) / CREDIT_USD, 2),
            }
            for r in rows
        ],
    }


@router.get("/routines", **typed_response(RoutinesOut))
async def list_routines(db: DbSession, auth: AuthUser) -> dict:
    """The person's routines, oldest first."""
    return ok(await _routines_out(db, auth.user_id))


async def _routine(db, user_id: int, routine_id: uuid.UUID):
    row = await routine_personal.one(db, user_id, routine_id)
    if row is None:
        raise NotFoundError.for_resource("routine", str(routine_id))
    return row


@router.post(
    "/routines/{routineId}/pause",
    dependencies=[ROUTE_DECIDES],
    **typed_response(RoutinesOut),
)
async def pause_routine(
    routine_id: Annotated[uuid.UUID, Path(alias="routineId")],
    db: DbSession,
    auth: AuthUser,
) -> dict:
    routine_personal.pause(await _routine(db, auth.user_id, routine_id))
    await db.commit()
    return ok(await _routines_out(db, auth.user_id))


@router.post(
    "/routines/{routineId}/resume",
    dependencies=[ROUTE_DECIDES],
    **typed_response(RoutinesOut),
)
async def resume_routine(
    routine_id: Annotated[uuid.UUID, Path(alias="routineId")],
    db: DbSession,
    auth: AuthUser,
) -> dict:
    """Running again from now; what it missed while paused is not made up."""
    routine_personal.resume(await _routine(db, auth.user_id, routine_id))
    await db.commit()
    return ok(await _routines_out(db, auth.user_id))


@router.delete(
    "/routines/{routineId}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(RoutinesOut),
)
async def delete_routine(
    routine_id: Annotated[uuid.UUID, Path(alias="routineId")],
    db: DbSession,
    auth: AuthUser,
) -> dict:
    await db.delete(await _routine(db, auth.user_id, routine_id))
    await db.commit()
    return ok(await _routines_out(db, auth.user_id))
