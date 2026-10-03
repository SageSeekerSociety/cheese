"""A person's conversations with their 芝士, and asking one question (#2285).

A question is answered by the conversation's own pi session on the session host
(``agent.harness.pi.personal``), which keeps the conversation: it is started
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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import spawn
from app.core.config import settings
from app.core.sandbox_auth import mint_personal_credential
from app.core.sentences import error_frame, say
from app.domain.agent.harness.pi.handless import (
    Answered,
    HandlessSessions,
    Looking,
    Said,
    SessionError,
)
from app.domain.agent.harness.pi.personal import Launch
from app.domain.assistant import billing, tools
from app.domain.assistant.asking import BUSY_SECONDS
from app.domain.assistant.models import AssistantConversation, AssistantMessage
from app.domain.assistant.prompt import earlier, system_prompt

logger = logging.getLogger(__name__)

TITLE_CHARS = 40

#: Said when the model could not be reached or failed mid-answer.
FAILED = say("assistantFailed")
#: The platform path the session's tools are called under.
TOOLS_PATH = "/assistant/tools"
#: How long before the conversation's hold lapses an answer is given up on, so
#: the session never reaches for the model outside the question's window.
MARGIN_S = 10.0


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
        self, conversation: AssistantConversation, role: str, text: str
    ) -> AssistantMessage:
        seq = (
            await self._s.scalar(
                select(func.coalesce(func.max(AssistantMessage.seq), 0)).where(
                    AssistantMessage.conversation_id == conversation.id
                )
            )
        ) or 0
        row = AssistantMessage(
            conversation_id=conversation.id, seq=seq + 1, role=role, text=text
        )
        self._s.add(row)
        conversation.last_active_at = datetime.now(UTC)
        if role == "user" and not conversation.title:
            conversation.title = text.strip().splitlines()[0][:TITLE_CHARS]
        await self._s.flush()
        return row


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def launch(user_id: int, conversation_id: uuid.UUID, place: str) -> Launch:
    """What the conversation's session is started with."""
    return Launch(
        user_id=user_id,
        conversation_id=conversation_id,
        system_prompt=system_prompt(place),
        tools_path=TOOLS_PATH,
        tools=tools.SPECS,
        token=mint_personal_credential(
            user_id=user_id, conversation_id=str(conversation_id)
        ),
        model=settings.assistant_model,
    )


async def ask(
    *,
    sessions: async_sessionmaker[AsyncSession],
    people: HandlessSessions,
    started: Launch,
    question: str,
    held_at: float,
    on_done: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[bytes]:
    """Answer ``question`` in the conversation, streamed as server-sent events:
    ``delta`` (text), ``tool`` (what 芝士 is looking at), ``error``, ``done``.

    The caller has already checked that the conversation is the person's, that
    they may see its place and have credits left, and has started the session
    (``started``) and taken the conversation's hold at ``held_at``."""
    user_id, conversation_id = started.user_id, started.conversation_id
    async with sessions() as session:
        store = AssistantConversations(session)
        conversation = await store.owned(user_id, conversation_id)
        before = await store.messages(conversation_id)
        await store.append(conversation, "user", question)
        await session.commit()

    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def run() -> None:
        answer, failure = "", None
        try:
            async for event in people.ask(
                started,
                uuid.uuid4(),
                question,
                earlier=earlier(before),
                ceiling_s=BUSY_SECONDS - MARGIN_S - (time.monotonic() - held_at),
            ):
                if isinstance(event, Said):
                    queue.put_nowait(sse("delta", {"text": event.text}))
                elif isinstance(event, Looking):
                    queue.put_nowait(sse("tool", {"name": event.tool}))
                elif isinstance(event, Answered):
                    answer, failure = event.text, event.error
                    if failure:
                        logger.warning(
                            "assistant answer failed conversation=%s: %s",
                            conversation_id,
                            failure,
                        )
        except SessionError as exc:
            logger.warning("assistant session failed: %s", exc)
            failure = str(exc)
        except Exception:  # noqa: BLE001 — the reader is told; the log keeps why
            logger.warning("assistant answer failed", exc_info=True)
            failure = FAILED
        finally:
            try:
                if answer and not failure:
                    async with sessions() as session:
                        store = AssistantConversations(session)
                        conversation = await store.owned(user_id, conversation_id)
                        await store.append(conversation, "assistant", answer)
                        await session.commit()
            except Exception:  # noqa: BLE001
                logger.warning("saving an assistant answer failed", exc_info=True)
            if failure:
                queue.put_nowait(sse("error", error_frame(FAILED)))
            if on_done is not None:
                await on_done()
            spawn(
                billing.settle(sessions, user_id),
                name=f"assistant-charge-{conversation_id}",
            )
            queue.put_nowait(sse("done", {}))
            queue.put_nowait(None)

    spawn(run(), name=f"assistant-answer-{conversation_id}")
    while (chunk := await queue.get()) is not None:
        yield chunk
