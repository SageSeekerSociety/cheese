"""A person's conversations with their 芝士, and asking one question (#2285).

A question is answered by the conversation's own pi session on the session host
(``personal.session``), which keeps the conversation: it is started
with the conversation's id, lets go when idle, and picks the conversation up
again when the next question starts it. ``AssistantMessage`` rows are what the
person said and was told, in order, and are what the panel shows.

Asking runs in a task of its own that feeds the response through a queue. The
reader can leave mid-answer — close the panel, lose the connection — and the
answer still finishes, is still saved for the next time they open the
conversation, and what it spent is still charged (``billing.settle``): it was
spent either way.

A conversation older than its session — one asked before 芝士 ran there — has
its earlier questions and answers given to the session with the first question
it hears (``prompt.earlier``), so it goes on from where it was.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
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
from app.domain.agent.session_host.answer import ask as ask_session
from app.domain.agent.session_host.contract import (
    Access,
    HostFull,
    Prompt,
    SessionError,
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
    The hold is also what lets the conversation's 芝士 reach the model at all
    (``api/routes/llm_proxy.py``): a model call is charged to the person, and
    only a question they asked may be charged to them. A stop said to an
    earlier question does not carry over to this one."""
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


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def ask(
    *,
    sessions: async_sessionmaker[AsyncSession],
    redis: Redis,
    people: SessionHost,
    user_id: int,
    conversation_id: uuid.UUID,
    started: tuple[SessionRef, SessionSpec, Access],
    question: str,
    held_at: float,
    on_done: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[bytes]:
    """Answer ``question`` in the conversation, streamed as server-sent events:
    ``queued`` (waiting for the session host), ``delta`` (text), ``tool`` (what
    芝士 is looking at), ``error``, ``done`` (``stopped``: the person stopped
    it, and what was written is kept).

    The caller has already checked that the conversation is the person's, that
    they may see its place and have credits left, and has taken the
    conversation's hold at ``held_at`` (``take_conversation``). The session is
    ``started`` (`personal.session`)."""

    async def stopped() -> bool:
        return bool(await redis.exists(_stop_key(conversation_id)))

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

    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def run() -> None:
        answer, written, failure, refusal = "", "", None, FAILED
        try:
            async for event in ask_session(
                people,
                *started,
                said,
                work_id=work,
                ceiling_s=ANSWER_S - (time.monotonic() - held_at),
                stopped=stopped,
            ):
                if isinstance(event, Waiting):
                    queue.put_nowait(sse("queued", {}))
                elif isinstance(event, Words):
                    written += event.text
                    queue.put_nowait(sse("delta", {"text": event.text}))
                elif isinstance(event, Tool):
                    queue.put_nowait(sse("tool", {"name": event.name}))
                elif isinstance(event, Answer):
                    answer, failure = event.text, event.error
                    if failure:
                        logger.warning(
                            "assistant answer failed conversation=%s: %s",
                            conversation_id,
                            failure,
                        )
        except HostFull:
            failure, refusal = "the session host stayed full", BUSY
        except StartAbandoned:
            pass
        except SessionError as exc:
            logger.warning("assistant session failed: %s", exc)
            failure = str(exc)
        except Exception:  # noqa: BLE001 — the reader is told; the log keeps why
            logger.warning("assistant answer failed", exc_info=True)
            failure = FAILED
        finally:
            was_stopped = await stopped()
            if was_stopped:
                # What it had written when it was stopped stays, marked as such.
                answer, failure = written.strip(), None
            try:
                if (answer and not failure) or was_stopped:
                    async with sessions() as session:
                        store = AssistantConversations(session)
                        conversation = await store.owned(user_id, conversation_id)
                        await store.append(
                            conversation, "assistant", answer, stopped=was_stopped
                        )
                        await session.commit()
            except Exception:  # noqa: BLE001
                logger.warning("saving an assistant answer failed", exc_info=True)
            if failure:
                queue.put_nowait(sse("error", error_frame(refusal)))
            if on_done is not None:
                await on_done()
            spawn(
                billing.settle(sessions, user_id),
                name=f"assistant-charge-{conversation_id}",
            )
            queue.put_nowait(sse("done", {"stopped": was_stopped}))
            queue.put_nowait(None)

    spawn(run(), name=f"assistant-answer-{conversation_id}")
    while (chunk := await queue.get()) is not None:
        yield chunk
