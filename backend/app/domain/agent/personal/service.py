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

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import func, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import model_roles
from app.core.background import spawn
from app.core.sandbox_auth import mint_delegated_credential
from app.core.sentences import NoticeText, error_frame, say
from app.domain.agent.admission import Hold, Slot, enter
from app.domain.agent.personal import billing, loops
from app.domain.agent.personal.keys import person_key
from app.domain.agent.personal.prompt import (
    about,
    earlier,
    files_said,
    now_line,
    page,
)
from app.domain.agent.personal.session import HOST_WAIT_S, ref
from app.domain.agent.session_host.answer import (
    PAST_CEILING,
    Answer,
    Said,
    Subagents,
    Thinking,
    Tool,
    ToolArgs,
    ToolDone,
    ToolFailed,
    Waiting,
    Words,
)
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
from app.domain.assistant import gate, proposals
from app.domain.assistant import memory as assistant_memory
from app.domain.assistant.models import AssistantConversation, AssistantMessage
from app.domain.notification.live import wake_after_commit
from app.domain.usage import pricing
from app.domain.usage.ledger import Ledger, Rates, payer_for_person
from app.domain.user.services import timezones_by_ids, usernames_by_ids

logger = logging.getLogger(__name__)

TITLE_CHARS = 40

#: Said when the model could not be reached or failed mid-answer.
FAILED = say("assistantFailed")
#: Said when the session host stayed full for as long as a question waits.
BUSY = say("assistantBusy")
#: Said when an answer was stopped for writing the same thing over and over.
REPEATING = say("assistantRepeating")
#: An answer stopped at its ceiling: what it wrote stays.
PAST_CEILING_NOTICE = say("assistantPastCeiling", hours=4)
#: How long one question may take, from when it took its conversation: room
#: for a long piece of work (marking forty reports, say), not a limit an answer
#: is meant to meet. The person can stop it at any time; near it the answer is
#: told to wrap up (``WRAP_UP_S``). A runner that went away is noticed within
#: ``GONE_AFTER_S``, and the conversation's hold is renewed only while the
#: answer is read.
ANSWER_S = 4 * 3600.0
#: How much longer than its life a question's credential lasts.
CREDENTIAL_MARGIN_S = 60
#: How long one credential a question's tools act with lasts. A long answer
#: is given a new one before then (``RENEW_AFTER_S``), so one that leaked is
#: good for an hour, not for the answer's whole four.
ACTING_S = 3600
RENEW_AFTER_S = 40 * 60
#: How long before its ceiling an answer is told to wrap up, and what it is
#: told: it writes down where it got to rather than being cut off mid-step.
WRAP_UP_S = 5 * 60
WRAP_UP = (
    "<平台>\n这一轮还剩不到 5 分钟就会停下。不要再开始新的步骤：用用户提问的语言写下"
    "已经做完的、结果在哪里、还没做的，作为这一轮的回答。\n</平台>"
)


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


@dataclass(frozen=True)
class Refused:
    """Why a question may not be asked now: ``status`` as a route says it,
    ``message`` to the person, and how long until it is worth asking again.
    ``credits``: the person has none left, as opposed to the platform not
    being able to answer now."""

    status: int
    message: NoticeText
    retry_after_s: int
    credits: bool = False


async def admit(
    session: AsyncSession,
    redis: Redis | None,
    people: SessionHost,
    *,
    user_id: int,
    conversation_id: uuid.UUID,
    sessions: async_sessionmaker[AsyncSession],
) -> Slot | Refused:
    """Whether a question may be asked in the conversation now, and the
    conversation's hold for it when it may: the model is priced, the person
    has a key and credits left, the session host is there, and no other
    question of the conversation is being answered. The person asking and a
    routine running on its own (``routine.personal``) are admitted alike.
    Commits ``session``."""
    rates = Rates.of(model_roles.model("assistant_model"), await pricing.model_rates())
    key = await person_key(session, user_id, sessions)
    if rates is None or key is None or not people.available():
        await session.commit()
        return Refused(503, say("assistantNotOpen"), 60)
    refused = await Ledger(session).admit(await payer_for_person(session, user_id))
    await session.commit()
    if refused is not None:
        return Refused(429, refused.message, refused.retry_after_s(), credits=True)
    slot = None if redis is None else await take_conversation(redis, conversation_id)
    if slot is None:
        return Refused(429, say("assistantStillAnswering"), 5)
    return slot


async def answering(redis: Redis, conversation_id: uuid.UUID | str) -> bool:
    """Is a question of this conversation being answered right now?"""
    return bool(await redis.exists(busy_key(conversation_id)))


def _stop_key(conversation_id: uuid.UUID | str) -> str:
    return f"assistant:stop:{conversation_id}"


def _page_key(conversation_id: uuid.UUID | str) -> str:
    return f"assistant:page:{conversation_id}"


#: How long a conversation is taken to remember the page it was last told: past
#: this the page is said again, whether or not it changed.
PAGE_REMEMBERED_S = 6 * 3600


async def page_to_say(redis: Redis, conversation_id: uuid.UUID, context: str) -> str:
    """The page to say with this question: all of it when it changed since the
    conversation was last told, nothing when it did not."""
    if not context:
        return ""
    digest = hashlib.sha256(context.encode()).hexdigest()
    said = await redis.get(_page_key(conversation_id))
    if (
        said is not None
        and (said.decode() if isinstance(said, bytes) else said) == digest
    ):
        return ""
    await redis.set(_page_key(conversation_id), digest, ex=PAGE_REMEMBERED_S)
    return context


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
    """Where a conversation was started. A person's conversations are one
    thread, each a topic in it, so a new one starts on the person
    (``personal``); older ones started on a 题目 or a draft keep saying so,
    which is the page a question in them defaults to."""

    kind: str
    id: str

    @classmethod
    def personal(cls, user_id: int) -> Place:
        return cls("personal", str(user_id))


def sse(event: str, data: dict, position: str | None = None) -> bytes:
    """One server-sent event; ``position`` is its place in the question's
    stream, for reading on from it."""
    head = f"id: {position}\n" if position is not None else ""
    body = json.dumps(data, ensure_ascii=False)
    return f"{head}event: {event}\ndata: {body}\n\n".encode()


class ConversationNotFound(Exception):
    """No such conversation for this person — theirs or nobody's, it is the same
    answer, so an id never confirms another person's conversation exists."""


#: How many topics the panel reads at a time, and how many of a topic's
#: messages: the rest are read as the person scrolls up to them.
TOPICS_PAGE = 30
MESSAGES_PAGE = 50


class AssistantConversations:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list(
        self,
        user_id: int,
        *,
        before: tuple[datetime, uuid.UUID] | None = None,
        limit: int = TOPICS_PAGE,
    ) -> tuple[list[AssistantConversation], bool]:
        """A page of this person's conversations, wherever they were started,
        latest first: the topics of their thread with 芝士, from just before
        ``before`` (the last of the page read before it), and whether there are
        older ones. One that never got a question (started, then refused for
        lack of credits, say) is not listed: it has nothing to go back to."""
        stmt = select(AssistantConversation).where(
            AssistantConversation.user_id == user_id,
            AssistantConversation.title != "",
        )
        if before is not None:
            stmt = stmt.where(
                tuple_(AssistantConversation.last_active_at, AssistantConversation.id)
                < tuple_(literal(before[0]), literal(before[1]))
            )
        stmt = stmt.order_by(
            AssistantConversation.last_active_at.desc(),
            AssistantConversation.id.desc(),
        ).limit(limit + 1)
        rows = list((await self._s.execute(stmt)).scalars())
        return rows[:limit], len(rows) > limit

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

    async def latest(
        self,
        conversation_id: uuid.UUID,
        *,
        before: int | None = None,
        limit: int = MESSAGES_PAGE,
    ) -> tuple[list[AssistantMessage], bool]:
        """The last ``limit`` messages of a conversation before message
        ``before`` (by its ``seq``; the latest when it is None), in order, and
        whether there are earlier ones."""
        stmt = select(AssistantMessage).where(
            AssistantMessage.conversation_id == conversation_id
        )
        if before is not None:
            stmt = stmt.where(AssistantMessage.seq < before)
        stmt = stmt.order_by(AssistantMessage.seq.desc()).limit(limit + 1)
        rows = list((await self._s.execute(stmt)).scalars())
        return rows[:limit][::-1], len(rows) > limit

    async def newest(self, user_id: int) -> tuple[uuid.UUID, uuid.UUID] | None:
        """The newest message in any of this person's conversations, and its
        conversation: a panel that last saw another one has something to read."""
        found = (
            await self._s.execute(
                select(AssistantMessage.id, AssistantMessage.conversation_id)
                .join(
                    AssistantConversation,
                    AssistantConversation.id == AssistantMessage.conversation_id,
                )
                .where(AssistantConversation.user_id == user_id)
                .order_by(AssistantMessage.created_at.desc())
                .limit(1)
            )
        ).first()
        return None if found is None else (found[0], found[1])

    async def append(
        self,
        conversation: AssistantConversation,
        role: str,
        text: str,
        *,
        stopped: bool = False,
        parts: dict | None = None,
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
            parts=parts,
        )
        self._s.add(row)
        # A panel open elsewhere (another tab, another device) reads it again.
        wake_after_commit(self._s, inbox={conversation.user_id}, pushable=set())
        conversation.last_active_at = datetime.now(UTC)
        if role == "user" and not conversation.title:
            words = text.strip() or "、".join((parts or {}).get("files") or [])
            if words:
                conversation.title = words.splitlines()[0][:TITLE_CHARS]
        await self._s.flush()
        return row


def parts_of(told: list[tuple[str, str, dict]]) -> dict | None:
    """What 芝士 did while answering, from what its viewers were told: each
    time it reasoned and for how long, each tool call with its arguments and
    whether it failed, each draft write, each publish, each file it handed the
    person, each rule it made. Kept with the answer, so the conversation shows them again."""
    steps: list[dict] = []
    by_call: dict[str, dict] = {}
    edits: list[dict] = []
    published: list[dict] = []
    proposed: list[dict] = []
    handed: list[dict] = []
    rules: list[dict] = []
    branches: list[dict] = []
    reasoning: dict | None = None

    def ms(position: str) -> int:
        return int(position.split("-", 1)[0])

    for position, event, data in told:
        if reasoning is not None and event not in (
            "tool_args",
            "tool_done",
            "subagents",
        ):
            # Reasoning lasts until the next thing it does.
            reasoning["ms"] = ms(position) - reasoning.pop("from")
            reasoning = None
        if event == "thinking":
            reasoning = {
                "call": f"thinking-{len(steps)}",
                "name": "thinking",
                "args": "",
                "from": ms(position),
                **({"at": data["at"]} if isinstance(data.get("at"), int) else {}),
            }
            steps.append(reasoning)
        elif event == "tool":
            call = str(data.get("call") or f"step-{len(steps)}")
            if call in by_call:
                continue
            step = {"call": call, "name": data.get("name") or "", "args": ""}
            if isinstance(data.get("at"), int):
                step["at"] = data["at"]
            by_call[call] = step
            steps.append(step)
        elif event == "tool_args" and data.get("call") in by_call:
            step = by_call[data["call"]]
            step["args"] = step["args"][: int(data.get("at") or 0)] + str(
                data.get("text") or ""
            )
        elif event == "tool_done" and data.get("call") in by_call:
            for named in ("shown", "effect"):
                if data.get(named):
                    by_call[data["call"]][named] = str(data[named])
        elif event == "tool_failed" and data.get("call") in by_call:
            by_call[data["call"]]["failed"] = str(data.get("text") or "")
        elif event == "change":
            edits.append(data)
        elif event == "published":
            published.append(data)
        elif event == "proposal":
            proposed.append(data)
        elif event == "handed":
            handed.append(data)
        elif event == "rule":
            rules.append(data)
        elif event == "subagents":
            branches = list(data.get("branches") or [])
    if reasoning is not None:
        reasoning.pop("from")
    if not (steps or edits or published or proposed or handed or rules):
        return None
    return {
        "steps": steps,
        "edits": edits,
        "published": published,
        **({"proposals": proposed} if proposed else {}),
        **({"handed": handed} if handed else {}),
        **({"rules": rules} if rules else {}),
        **({"subagents": branches} if branches else {}),
    }


#: What a question to a person's 芝士 is, to the questions the platform reads
#: to the end (``session_host.consumptions``); its key is the conversation.
KIND = "personal"


#: What is done when a question asked by a routine (``ask``'s ``run``) has
#: ended (``routine.personal.ended``): given the run, the answer, and whether
#: it failed or was stopped.
RunEnded = Callable[[uuid.UUID, Answer, bool, bool], Awaitable[None]]
#: What is done when a person's own question that took a while was answered
#: while they were not looking (``routine.personal.answered``): given the
#: person, the conversation, the answer's words, how many things it left for
#: them to confirm, and the question's id.
Answered = Callable[[int, uuid.UUID, str, int, str], Awaitable[None]]

#: An answer that took at least this long is told to a person not looking at
#: it when it ends; a quick one they are waiting for.
TELL_AFTER_S = 120
#: How long a panel's word that it is showing a conversation holds: it says so
#: again sooner than this while it does (``looking``).
LOOKING_S = 30


def _looking_key(conversation_id: str) -> str:
    return f"assistant:looking:{conversation_id}"


async def looking(redis: Redis, conversation_id: uuid.UUID) -> None:
    """A panel is showing this conversation to its person right now."""
    await redis.set(_looking_key(str(conversation_id)), "1", ex=LOOKING_S)


def _acting(consumption: Consumption) -> str:
    """A new credential for an answer's tools, as the one it was asked with."""
    acting = consumption.data.get("acting") or {}
    return mint_delegated_credential(
        user_id=int(consumption.data["user"]),
        handle=str(acting.get("handle") or consumption.data["user"]),
        work=consumption.work_id,
        project_id=acting.get("project"),
        ttl_s=ACTING_S + CREDENTIAL_MARGIN_S,
        read_only=False,
        conversation=consumption.key,
    )


class Answers:
    """What becomes of a question's answer: shown as it is written, kept in the
    conversation, and charged to the person; and, for one a routine asked,
    the run told it ended (``run_ended``)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        consumptions: Consumptions,
        redis: Callable[[], Redis | None],
        run_ended: RunEnded | None = None,
        answered: Answered | None = None,
    ) -> None:
        self._sessions = sessions
        self._consumptions = consumptions
        self._redis = redis
        self._run_ended = run_ended
        self._answered = answered
        # When the credential of each answer read here was last given, on
        # this process's clock, and which were told to wrap up.
        self._acting_since: dict[str, float] = {}
        self._wrapping_up: set[str] = set()
        # What each answer read here has written, and which were stopped for
        # writing the same thing over and over (``loops``).
        self._written: dict[str, tuple[str, int]] = {}
        self._repeating: set[str] = set()

    async def stopped(self, consumption: Consumption) -> bool:
        redis = self._redis()
        return redis is not None and bool(
            await redis.exists(_stop_key(consumption.key))
        )

    async def took(
        self,
        consumption: Consumption,
        item: Waiting | Said,
    ) -> None:
        if not isinstance(item, Waiting):
            await self._keep_going(consumption)
        if isinstance(item, Waiting):
            await self._consumptions.publish(consumption, "queued", {})
        elif isinstance(item, Words):
            await self._consumptions.publish(
                consumption, "delta", {"text": item.text, "at": item.at}
            )
            await self._look_for_loops(consumption, item.text)
        elif isinstance(item, Tool):
            # ``at``: how much of the answer's text came before it, so the
            # page shows the call between what was said before and after.
            await self._consumptions.publish(
                consumption,
                "tool",
                {"name": item.name, "call": item.call, "at": item.at},
            )
        elif isinstance(item, Subagents):
            # The branches it started, how each is going and what it did.
            await self._consumptions.publish(
                consumption, "subagents", {"branches": list(item.branches)}
            )
        elif isinstance(item, ToolDone):
            await self._consumptions.publish(
                consumption,
                "tool_done",
                {
                    "call": item.call,
                    **({"shown": item.shown} if item.shown else {}),
                    **({"effect": item.effect} if item.effect else {}),
                },
            )
        elif isinstance(item, Thinking):
            # That it reasons, never what: the page shows 思考中.
            await self._consumptions.publish(consumption, "thinking", {"at": item.at})
        elif isinstance(item, ToolArgs):
            # A call's arguments as they are written: the page shows a form
            # field filling with the value before the call writes it.
            await self._consumptions.publish(
                consumption,
                "tool_args",
                {"call": item.call, "text": item.text, "at": item.at},
            )
        elif isinstance(item, ToolFailed):
            await self._consumptions.publish(
                consumption, "tool_failed", {"call": item.call, "text": item.text}
            )

    async def _keep_going(self, consumption: Consumption) -> None:
        """What a long answer needs while it is read: a new credential before
        the one it acts with runs out, and, near its ceiling, a word to wrap
        up. Each is said once, and neither stops the answer if it fails."""
        work, now = consumption.work_id, time.monotonic()
        since = self._acting_since.get(work)
        if since is None and consumption.deadline is not None:
            # Read here first: its credential was given when it was said, at
            # the latest a ceiling before its deadline. Counted from then.
            said = consumption.deadline - ANSWER_S
            since = now - max(0.0, time.time() - said)
            self._acting_since[work] = since
        if since is not None and now - since >= RENEW_AFTER_S:
            self._acting_since[work] = now
            try:
                await self._consumptions.act_as(consumption, _acting(consumption))
            except Exception:
                logger.warning("renewing answer %s's credential failed", work)
        due = consumption.deadline
        if (
            due is not None
            and work not in self._wrapping_up
            and due - time.time() <= WRAP_UP_S
        ):
            self._wrapping_up.add(work)
            try:
                await self._consumptions.steer(consumption, WRAP_UP)
            except Exception:
                logger.warning("telling answer %s to wrap up failed", work)

    async def _unseen(self, consumption: Consumption) -> bool:
        """Whether an answer ended that the person should be told of: it took
        a while, and no panel is showing its conversation now."""
        asked_at = consumption.data.get("asked_at")
        if self._answered is None or not isinstance(asked_at, (int, float)):
            return False
        if time.time() - asked_at < TELL_AFTER_S:
            return False
        redis = self._redis()
        return redis is None or not await redis.exists(_looking_key(consumption.key))

    async def _look_for_loops(self, consumption: Consumption, more: str) -> None:
        work = consumption.work_id
        text, looked = self._written.get(work, ("", 0))
        text += more
        if len(text) - looked >= loops.LOOK_EVERY:
            looked = len(text)
            if work not in self._repeating and loops.repeating(text):
                self._repeating.add(work)
                await self._consumptions.halt(consumption)
        self._written[work] = (text, looked)

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
        self._written.pop(consumption.work_id, None)
        self._acting_since.pop(consumption.work_id, None)
        self._wrapping_up.discard(consumption.work_id)
        if consumption.work_id in self._repeating:
            self._repeating.discard(consumption.work_id)
            failed, refusal = "the answer kept saying the same thing", REPEATING
        elif isinstance(failure, HostFull):
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
        elif failed == PAST_CEILING:
            # Told to wrap up before it, it wrote down where it got to: that
            # stays, and the person is told why it ended there.
            text, refusal = written.strip(), PAST_CEILING_NOTICE
        parts = parts_of(await self._consumptions.told(consumption.work_id))
        if parts and not failed and not stopped and not text.strip():
            # It worked and then wrote nothing: the page says so rather than
            # showing an answer with no words.
            parts = {**parts, "silent": True}
        if (text and failed in (None, PAST_CEILING)) or stopped or parts:
            if answer.usage is not None and answer.usage.calls:
                # Each model call's prompt and how much of it the provider's
                # cache answered: how well the conversation keeps its start.
                parts = {
                    **(parts or {}),
                    "usage": {"calls": [list(c) for c in answer.usage.calls]},
                }
            async with self._sessions() as session:
                store = AssistantConversations(session)
                conversation = await store.owned(user_id, conversation_id)
                await store.append(
                    conversation,
                    "assistant",
                    "" if failed and not stopped and failed != PAST_CEILING else text,
                    stopped=stopped,
                    parts=parts,
                )
                await session.commit()
        spawn(
            billing.settle(
                self._sessions,
                user_id,
                str(consumption.data.get("billing") or billing.ASKING),
            ),
            name=f"assistant-charge-{conversation_id}",
        )
        run = consumption.data.get("run")
        if run and self._run_ended is not None:
            try:
                await self._run_ended(
                    uuid.UUID(str(run)), answer, bool(failed), stopped
                )
            except Exception:  # the answer is kept either way
                logger.exception("ending routine run %s failed", run)
        elif not failed and not stopped and await self._unseen(consumption):
            try:
                assert self._answered is not None
                asks = sum(
                    1
                    for p in (parts or {}).get("proposals") or []
                    if p.get("state", "pending") == "pending"
                )
                await self._answered(
                    user_id, conversation_id, text, asks, consumption.work_id
                )
            except Exception:  # the answer is kept either way
                logger.exception("telling of answer %s failed", consumption.work_id)
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
    context: str = "",
    kind: str = billing.ASKING,
    files: list[tuple[str, str]] | None = None,
    project: uuid.UUID | None = None,
    run: uuid.UUID | None = None,
    shown: dict | None = None,
    told: str | None = None,
    cited: str = "",
    asked_with: dict | None = None,
    kept: bool = False,
) -> Consumption:
    """Ask ``question`` in the conversation; the answer is read to its end in
    the background (``Answers``), and what its readers see of it is the
    question's stream: ``queued`` (waiting for the session host), ``delta``
    (text), ``tool`` (what 芝士 is looking at), ``error``, ``done``
    (``stopped``: the person stopped it, and what was written is kept).

    The caller has already checked that the conversation is the person's, that
    they may see its place and have credits left, and has taken the
    conversation's hold at ``held_at`` (``take_conversation``). The session is
    ``started`` (`personal.session`). ``context`` is the page the person has
    open, when the conversation has not been told it yet (``page_to_say``);
    ``kind`` the line its spend is charged under (``billing``); ``files`` the
    ones handed over with it, as (the ref 芝士 reads it by, name); ``project`` the one the page is
    in, which its tools are let into as the person (and into no other).

    ``run`` is a routine's run asking on its own (``routine.personal``): what
    it says is the platform's, not the person's (``role`` ``platform``), and
    the run is told when the answer ends. Its id is the answer's ``work``, so
    the cards the run puts up are found by it. ``shown`` is how the panel
    names the routine, in place of what the platform told 芝士. ``told`` is
    the question as 芝士 reads it, when that differs from how it is kept (what
    it cites, by name); ``cited`` what it cites, written out after it;
    ``asked_with`` is kept with the question: where it was asked, the text
    selected, what it cites. ``kept``: the question is in the conversation
    already (said mid-answer, and the answer ended before 芝士 heard it), so
    it is not written down again."""
    work = run or uuid.uuid4()
    async with sessions() as session:
        store = AssistantConversations(session)
        conversation = await store.owned(user_id, conversation_id)
        before = await store.messages(conversation_id)
        # Files handed over alone are the whole question: they are kept
        # with it, so the conversation shows them when it is read again.
        if not kept:
            await store.append(
                conversation,
                "platform" if run else "user",
                question,
                parts={
                    **({"files": [name for _, name in files]} if files else {}),
                    **({"shown": shown} if shown and not files else {}),
                    **(asked_with or {}),
                }
                or None,
            )
        handle = (await usernames_by_ids(session, [user_id])).get(user_id)
        timezone = (await timezones_by_ids(session, [user_id])).get(user_id)
        # What it remembers of them goes with the start of a conversation, never
        # in the rules, which stay the same for everyone.
        remembered = (
            await assistant_memory.index(session, handle)
            if handle and (await gate.rules_of(session, user_id)).memory
            else None
        )
        # Cards the person decided on after the answer that put them up had
        # ended: that answer never heard how they ended, so this one is told.
        decided = await proposals.untold(
            session,
            user_id,
            [
                card["id"]
                for m in before
                for card in (m.parts or {}).get("proposals") or []
                if isinstance(card, dict) and isinstance(card.get("id"), int)
            ],
        )
        for card in decided:
            proposals.told(card)
        await session.commit()

    async def said() -> Prompt:
        # What its tools act with while answering: this person's own
        # permissions, for no longer than the answer may take from when the
        # session is there, and in the project of the page if it is in one.
        acting = mint_delegated_credential(
            user_id=user_id,
            handle=handle or str(user_id),
            work=str(work),
            project_id=None if project is None else str(project),
            ttl_s=ACTING_S + CREDENTIAL_MARGIN_S,
            read_only=False,
            conversation=str(conversation_id),
        )
        # The page goes with the question itself: a preface is said only to a
        # session that has not been spoken to yet.
        said = "\n\n".join(
            part for part in (page(context), told or question, cited) if part
        )
        said += files_said(files or [])
        if decided:
            said += "\n\n你之前请用户确认的操作，结果如下：\n" + "\n".join(
                f"- {proposals.outcome(card)}" for card in decided
            )
        # The clock goes at the end of the question, never in the rules: what
        # the session sends first stays the same all day.
        said += "\n\n" + now_line(timezone)
        preface = "\n\n".join(
            part
            for part in (
                about(handle or str(user_id), timezone, remembered),
                earlier(before),
            )
            if part
        )
        return Prompt(work, said, acting=acting, preface=preface)

    return await consumptions.begin(
        kind=KIND,
        key=str(conversation_id),
        data={
            "user": user_id,
            "billing": kind,
            "asked_at": time.time(),
            # Who its tools act as, so whoever reads the answer can renew it.
            "acting": {
                "handle": handle or str(user_id),
                "project": None if project is None else str(project),
            },
            **({"run": str(run)} if run else {}),
        },
        work_id=work,
        session=started,
        prompt=said,
        ceiling_s=lambda: ANSWER_S - (time.monotonic() - held_at),
        slots=[slot],
    )
