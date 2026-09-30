"""A person's conversations with their 芝士, and asking one question (#2285).

Asking runs in a task of its own that feeds the response through a queue. The
reader can leave mid-answer — close the panel, lose the connection — and the run
still finishes, the answer is still saved for the next time they open the
conversation, and the tokens it spent are still charged: they were spent either
way.

Charging happens once, after the run, from the usage the gateway reported for
every round (tool calls included, cache shares priced at the cache rate).

The model is not sent the whole conversation forever. When a question's prompt
grows past ``assistant_history_cap_tokens``, everything before the last few
questions is folded into a summary by one more call (charged with the question
that triggered it), and the model's copy of the history restarts from the
summary plus those questions. History is only ever appended to between two
foldings, which keeps the prompt's prefix stable for the provider's cache; a
sliding window would change the prefix on every question and miss the cache
every time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic_ai import Agent
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.usage import RunUsage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import spawn
from app.core.config import settings
from app.domain.assistant import agent as assistant_agent
from app.domain.assistant.models import AssistantConversation, AssistantMessage
from app.domain.usage.personal import PersonalCredits, Rates

logger = logging.getLogger(__name__)

#: How many of the latest questions stay verbatim when the rest is folded.
KEEP_QUESTIONS = 2
TITLE_CHARS = 40

#: Said when the model could not be reached or failed mid-answer.
FAILED = "芝士暂时答不上来，稍后再试。"


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
            history=[],
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


def _as_text(messages: list[ModelMessage]) -> str:
    """The spoken part of a history, for the summariser: what was asked and
    answered, not the tool traffic in between."""
    lines = []
    for message in messages:
        for part in message.parts:
            if isinstance(message, ModelRequest) and isinstance(part, UserPromptPart):
                content = part.content if isinstance(part.content, str) else ""
                lines.append(f"用户：{content}")
            elif isinstance(message, ModelResponse) and isinstance(part, TextPart):
                lines.append(f"芝士：{part.content}")
    return "\n".join(lines)


def _split_for_folding(messages: list[ModelMessage]) -> int:
    """Where the kept tail starts: at the request that asked the
    ``KEEP_QUESTIONS``-th last question, so a tool call is never separated from
    its result."""
    starts = [
        i
        for i, m in enumerate(messages)
        if isinstance(m, ModelRequest)
        and any(isinstance(p, UserPromptPart) for p in m.parts)
    ]
    if len(starts) <= KEEP_QUESTIONS:
        return 0
    return starts[-KEEP_QUESTIONS]


def _last_prompt_tokens(messages: list[ModelMessage]) -> int:
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            return message.usage.input_tokens
    return 0


async def _fold(
    key: str, summary: str, messages: list[ModelMessage], conversation_id: uuid.UUID
) -> tuple[str, list[ModelMessage], RunUsage] | None:
    """Summarise all but the latest questions; None when there is nothing to
    fold or the summary could not be written (the history is kept whole)."""
    cut = _split_for_folding(messages)
    if cut == 0:
        return None
    summariser: Agent[None, str] = Agent(
        assistant_agent.model(key), model_settings=assistant_agent.model_settings()
    )
    prompt = (
        "把下面这段用户和芝士的对话压缩成一份摘要，供芝士之后接着对话时参考。"
        "保留：用户的情况和偏好、问过的问题和得到的结论、还没解决的事。"
        "不超过 400 字，只写摘要本身。\n\n"
        + (f"<更早的摘要>\n{summary}\n</更早的摘要>\n\n" if summary else "")
        + "<对话>\n"
        + _as_text(messages[:cut])
        + "\n</对话>"
    )
    try:
        result = await summariser.run(prompt)
    except Exception:  # noqa: BLE001 — a failed fold only means a longer prompt
        logger.warning(
            "folding assistant conversation %s failed", conversation_id, exc_info=True
        )
        return None
    return result.output.strip(), messages[cut:], result.usage


async def ask(
    *,
    sessions: async_sessionmaker[AsyncSession],
    key: str,
    rates: Rates,
    user_id: int,
    conversation_id: uuid.UUID,
    question: str,
    place: str,
    on_done: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[bytes]:
    """Answer ``question`` in the conversation, streamed as server-sent events:
    ``delta`` (text), ``tool`` (what 芝士 is looking at), ``error``, ``done``.

    The caller has already checked that the conversation is the person's, that
    they may see its place, and that they have credits left."""
    async with sessions() as session:
        store = AssistantConversations(session)
        conversation = await store.owned(user_id, conversation_id)
        await store.append(conversation, "user", question)
        history = ModelMessagesTypeAdapter.validate_python(conversation.history)
        summary = conversation.summary
        await session.commit()

    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def run() -> None:
        answer: list[str] = []
        result = None
        try:
            async with assistant_agent.build(key).run_stream_events(
                question,
                message_history=history,
                instructions=assistant_agent.instructions(place=place, summary=summary),
                deps=assistant_agent.Deps(user_id=user_id, sessions=sessions),
            ) as events:
                async for event in events:
                    if isinstance(event, PartStartEvent) and isinstance(
                        event.part, TextPart
                    ):
                        if event.part.content:
                            answer.append(event.part.content)
                            queue.put_nowait(sse("delta", {"text": event.part.content}))
                    elif isinstance(event, PartDeltaEvent) and isinstance(
                        event.delta, TextPartDelta
                    ):
                        answer.append(event.delta.content_delta)
                        queue.put_nowait(
                            sse("delta", {"text": event.delta.content_delta})
                        )
                    elif isinstance(event, FunctionToolCallEvent):
                        queue.put_nowait(sse("tool", {"name": event.part.tool_name}))
                    elif isinstance(event, AgentRunResultEvent):
                        result = event.result
        except Exception:  # noqa: BLE001 — the reader is told; the log keeps why
            logger.warning("assistant answer failed", exc_info=True)
            queue.put_nowait(sse("error", {"message": FAILED}))
        finally:
            try:
                await _settle(
                    sessions=sessions,
                    key=key,
                    rates=rates,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    answer="".join(answer) if result is not None else "",
                    messages=result.all_messages() if result is not None else None,
                    usage=result.usage if result is not None else None,
                    summary=summary,
                )
            except Exception:  # noqa: BLE001
                logger.warning("settling an assistant answer failed", exc_info=True)
            if on_done is not None:
                await on_done()
            queue.put_nowait(sse("done", {}))
            queue.put_nowait(None)

    spawn(run(), name=f"assistant-answer-{conversation_id}")
    while (chunk := await queue.get()) is not None:
        yield chunk


async def _settle(
    *,
    sessions: async_sessionmaker[AsyncSession],
    key: str,
    rates: Rates,
    user_id: int,
    conversation_id: uuid.UUID,
    answer: str,
    messages: list[ModelMessage] | None,
    usage: RunUsage | None,
    summary: str,
) -> None:
    """Save the answer and the model's history, fold it if it grew past the cap,
    and charge the person for everything this question spent."""
    spent = [usage] if usage is not None else []
    if messages is not None and (
        _last_prompt_tokens(messages) > settings.assistant_history_cap_tokens
    ):
        folded = await _fold(key, summary, messages, conversation_id)
        if folded is not None:
            summary, messages, fold_usage = folded
            spent.append(fold_usage)

    async with sessions() as session:
        store = AssistantConversations(session)
        conversation = await store.owned(user_id, conversation_id)
        if answer:
            await store.append(conversation, "assistant", answer)
        if messages is not None:
            conversation.history = json.loads(
                ModelMessagesTypeAdapter.dump_json(messages)
            )
            conversation.summary = summary
        credits = PersonalCredits(session)
        for u in spent:
            if u.input_tokens or u.output_tokens:
                await credits.charge(
                    user_id,
                    model=settings.assistant_model,
                    rates=rates,
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_read_tokens=u.cache_read_tokens,
                    cache_write_tokens=u.cache_write_tokens,
                    kind="assistant",
                )
        await session.commit()
