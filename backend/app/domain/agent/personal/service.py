"""A person's conversations with their 芝士, and asking one question (#2285).

A question is answered by the conversation's own pi session on the session host
(``personal.session``), which keeps the conversation: it is started
with the conversation's id, lets go when idle, and picks the conversation up
again when the next question starts it. ``AssistantMessage`` rows are what the
person said and was told, in order, and are what the panel shows.

A question is read to its end by whichever backend process is up when it ends
(``session_host.consumptions``), not by the request that asked it. The reader
can leave mid-answer — close the panel, lose the connection, have the backend
restart under it — and the answer still finishes, is still saved, and what it
spent is still charged (``billing.settle``): it was spent either way. A reader
that comes back reads the rest of the question's stream from where it left.

A conversation older than its session — one asked before 芝士 ran there — has
its earlier questions and answers given to the session with the first question
it hears (``prompt.earlier``), so it goes on from where it was.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import spawn
from app.core.sandbox_auth import mint_delegated_credential
from app.core.sentences import error_frame, say
from app.domain.agent.admission import Hold, Slot, enter
from app.domain.agent.personal import billing
from app.domain.agent.personal.prompt import earlier
from app.domain.agent.personal.session import HOST_WAIT_S, ref
from app.domain.agent.session_host.answer import Answer, Tool, Waiting, Words
from app.domain.agent.session_host.consumptions import Consumption, Consumptions
from app.domain.agent.session_host.contract import (
    Access,
    HostFull,
    Prompt,
    SessionRef,
    SessionSpec,
    StartAbandoned,
)
from app.domain.agent.session_host.host import SessionHost
from app.domain.assistant.models import AssistantConversation, AssistantMessage
from app.domain.user.services import usernames_by_ids

logger = logging.getLogger(__name__)

TITLE_CHARS = 40

#: Said when the model could not be reached or failed mid-answer.
FAILED = say("assistantFailed")
#: Said when the session host stayed full for as long as a question waits.
BUSY = say("assistantBusy")
#: How long one question may take, from when it took its conversation.
ANSWER_S = 170.0
#: How much longer than that a question's credential lasts.
CREDENTIAL_MARGIN_S = 60


def busy_key(conversation_id: uuid.UUID | str) -> str:
    return f"assistant:busy:{conversation_id}"


async def take_conversation(redis: Redis, conversation_id: uuid.UUID) -> Slot | None:
    """Take the conversation for one question; None while another holds it.
    A stop said to an earlier question does not carry over to this one."""
    slot = await enter(
        redis, str(uuid.uuid4()), hold=Hold(busy_key(conversation_id)), wait_s=0
    )
    if slot is not None:
        await redis.delete(_stop_key(conversation_id))
    return slot


async def answering(redis: Redis, conversation_id: uuid.UUID | str) -> bool:
    """Is a question of this conversation being answered right now?"""
    return bool(await redis.exists(busy_key(conversation_id)))


def _stop_key(conversation_id: uuid.UUID | str) -> str:
    return f"assistant:stop:{conversation_id}"


async def stop(
    redis: Redis, people: SessionHost, user_id: int, conversation_id: uuid.UUID
) -> None:
    """Stop the conversation's question: its wait for the session host, or the
    answer being written. What was written is kept, marked as stopped."""
    await redis.set(
        _stop_key(conversation_id),
        "1",
        ex=int(HOST_WAIT_S + ANSWER_S) + CREDENTIAL_MARGIN_S,
    )
    if await answering(redis, conversation_id):
        await people.stop(ref(user_id, conversation_id))


@dataclass(frozen=True)
class Place:
    """Where a conversation was started: ``("task", "7")`` today."""

    kind: str
    id: str


def sse(event: str, data: dict, position: str | None = None) -> bytes:
    """One server-sent event; ``position`` is its place in the question's
    stream, for reading on from it."""
    head = f"id: {position}\n" if position is not None else ""
    body = json.dumps(data, ensure_ascii=False)
    return f"{head}event: {event}\ndata: {body}\n\n".encode()


class ConversationNotFound(Exception):
    """No such conversation for this person — theirs or nobody's, it is the same
    answer, so an id never confirms another person's conversation exists."""


class AssistantConversations:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list(self, user_id: int, place: Place) -> list[AssistantConversation]:
        """This person's conversations here, latest first. One that never got a
        question (started, then refused for lack of credits, say) is not
        listed: it has nothing to go back to."""
        stmt = (
            select(AssistantConversation)
            .where(
                AssistantConversation.user_id == user_id,
                AssistantConversation.place_kind == place.kind,
                AssistantConversation.place_id == place.id,
                AssistantConversation.title != "",
            )
            .order_by(AssistantConversation.last_active_at.desc())
            .limit(50)
        )
        return list((await self._s.execute(stmt)).scalars())

    async def start(self, user_id: int, place: Place) -> AssistantConversation:
        row = AssistantConversation(
            user_id=user_id,
            place_kind=place.kind,
            place_id=place.id,
            last_active_at=datetime.now(UTC),
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def owned(
        self, user_id: int, conversation_id: uuid.UUID
    ) -> AssistantConversation:
        row = await self._s.get(AssistantConversation, conversation_id)
        if row is None or row.user_id != user_id:
            raise ConversationNotFound(conversation_id)
        return row

    async def question_counts(
        self, conversation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        """How many questions each conversation holds, for the list."""
        if not conversation_ids:
            return {}
        rows = await self._s.execute(
            select(AssistantMessage.conversation_id, func.count())
            .where(
                AssistantMessage.conversation_id.in_(conversation_ids),
                AssistantMessage.role == "user",
            )
            .group_by(AssistantMessage.conversation_id)
        )
        return {cid: int(n) for cid, n in rows}

    async def messages(self, conversation_id: uuid.UUID) -> list[AssistantMessage]:
        stmt = (
            select(AssistantMessage)
            .where(AssistantMessage.conversation_id == conversation_id)
            .order_by(AssistantMessage.seq)
        )
        return list((await self._s.execute(stmt)).scalars())

    async def append(
        self,
        conversation: AssistantConversation,
        role: str,
        text: str,
        *,
        stopped: bool = False,
    ) -> AssistantMessage:
        seq = (
            await self._s.scalar(
                select(func.coalesce(func.max(AssistantMessage.seq), 0)).where(
                    AssistantMessage.conversation_id == conversation.id
                )
            )
        ) or 0
        row = AssistantMessage(
            conversation_id=conversation.id,
            seq=seq + 1,
            role=role,
            text=text,
            stopped=stopped,
        )
        self._s.add(row)
        conversation.last_active_at = datetime.now(UTC)
        if role == "user" and not conversation.title:
            conversation.title = text.strip().splitlines()[0][:TITLE_CHARS]
        await self._s.flush()
        return row


#: What a question to a person's 芝士 is, to the questions the platform reads
#: to the end (``session_host.consumptions``); its key is the conversation.
KIND = "personal"


class Answers:
    """What becomes of a question's answer: shown as it is written, kept in the
    conversation, and charged to the person."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        consumptions: Consumptions,
        redis: Callable[[], Redis | None],
    ) -> None:
        self._sessions = sessions
        self._consumptions = consumptions
        self._redis = redis

    async def stopped(self, consumption: Consumption) -> bool:
        redis = self._redis()
        return redis is not None and bool(
            await redis.exists(_stop_key(consumption.key))
        )

    async def took(
        self, consumption: Consumption, item: Waiting | Words | Tool
    ) -> None:
        if isinstance(item, Waiting):
            await self._consumptions.publish(consumption, "queued", {})
        elif isinstance(item, Words):
            await self._consumptions.publish(
                consumption, "delta", {"text": item.text, "at": item.at}
            )
        else:
            await self._consumptions.publish(consumption, "tool", {"name": item.name})

    async def ended(
        self,
        consumption: Consumption,
        answer: Answer,
        *,
        written: str,
        stopped: bool,
        failure: BaseException | None,
    ) -> list[tuple[str, dict]]:
        user_id = int(consumption.data["user"])
        conversation_id = uuid.UUID(consumption.key)
        text, refusal = answer.text, FAILED
        failed: str | None = answer.error
        if isinstance(failure, HostFull):
            failed, refusal = "the session host stayed full", BUSY
        elif isinstance(failure, StartAbandoned):
            failed = None
        elif failure is not None:
            logger.warning("assistant answer failed", exc_info=failure)
        elif failed:
            logger.warning(
                "assistant answer failed conversation=%s: %s", conversation_id, failed
            )
        if stopped:
            # What it had written when it was stopped stays, marked as such.
            text, failed = written.strip(), None
        if (text and not failed) or stopped:
            async with self._sessions() as session:
                store = AssistantConversations(session)
                conversation = await store.owned(user_id, conversation_id)
                await store.append(conversation, "assistant", text, stopped=stopped)
                await session.commit()
        spawn(
            billing.settle(self._sessions, user_id),
            name=f"assistant-charge-{conversation_id}",
        )
        closing = [("error", error_frame(refusal))] if failed else []
        return [*closing, ("done", {"stopped": stopped})]


async def ask(
    *,
    sessions: async_sessionmaker[AsyncSession],
    consumptions: Consumptions,
    user_id: int,
    conversation_id: uuid.UUID,
    started: tuple[SessionRef, SessionSpec, Access],
    question: str,
    held_at: float,
    slot: Slot,
) -> Consumption:
    """Ask ``question`` in the conversation; the answer is read to its end in
    the background (``Answers``), and what its readers see of it is the
    question's stream: ``queued`` (waiting for the session host), ``delta``
    (text), ``tool`` (what 芝士 is looking at), ``error``, ``done``
    (``stopped``: the person stopped it, and what was written is kept).

    The caller has already checked that the conversation is the person's, that
    they may see its place and have credits left, and has taken the
    conversation's hold at ``held_at`` (``take_conversation``). The session is
    ``started`` (`personal.session`)."""
    work = uuid.uuid4()
    async with sessions() as session:
        store = AssistantConversations(session)
        conversation = await store.owned(user_id, conversation_id)
        before = await store.messages(conversation_id)
        await store.append(conversation, "user", question)
        handle = (await usernames_by_ids(session, [user_id])).get(user_id)
        await session.commit()

    async def said() -> Prompt:
        # What its tools read with while answering: this person's own view, for
        # no longer than the answer may take from when the session is there,
        # and nothing it could change.
        acting = mint_delegated_credential(
            user_id=user_id,
            handle=handle or str(user_id),
            work=str(work),
            ttl_s=int(ANSWER_S) + CREDENTIAL_MARGIN_S,
        )
        return Prompt(work, question, acting=acting, preface=earlier(before))

    return await consumptions.begin(
        kind=KIND,
        key=str(conversation_id),
        data={"user": user_id},
        work_id=work,
        session=started,
        prompt=said,
        ceiling_s=lambda: ANSWER_S - (time.monotonic() - held_at),
        slots=[slot],
    )
