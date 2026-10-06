"""A document comment that names the document's agent is answered by the
comment thread's own session.

Comments are passive: recording one starts nothing. A comment, or a reply in a
comment thread, that @-mentions an agent the document can ask (its task's
agent; the project's own, for a document of no task) is a question to that
agent. It is answered in the background, after the comment is committed:

1. **Its turn.** One question of a thread is answered at a time, and at most
   ``question.ANSWERING_PER_PROJECT`` of a project's conversations are being
   answered at once; a question waits for its turn up to ``question.WAIT_S``.
2. **Admission.** The agent's model and the project's credits, as a turn of the
   agent would be admitted (``question.admit``).
3. **The question**, assembled fresh: the thread so far, the passage it is
   anchored to and the section around it, the whole document, the task's recent
   messages for a task's document (``thread_question``).
4. **The answer** becomes the agent's reply in the thread (``Replies``). Once
   asked, it is read to its end by whichever backend is up
   (``session_host.consumptions``), so a restart mid-answer still replies.

Whoever has the document open hears how far a thread's question has got as it
goes (``comment_activity`` frames: waiting for a session, answering, which tool
it used), and the thread list says it again for a page that opens meanwhile
(``question.answering``).

What the answer spent is drained into the project's usage once it is done,
answered or not: it was spent either way.
"""

import logging
import uuid
from dataclasses import asdict

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import spawn
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.redis import get_redis_client
from app.domain.agent.chat import ChatService
from app.domain.agent.document import question
from app.domain.agent.document.question import Asked, Bound, Surroundings
from app.domain.agent.document.session import ref, session_for
from app.domain.agent.session_host.answer import Answer, Tool, Waiting, Words
from app.domain.agent.session_host.consumptions import Consumption, Consumptions
from app.domain.agent.session_host.contract import (
    HostFull,
    Prompt,
    StartAbandoned,
)
from app.domain.agent.session_host.host import SessionHost
from app.domain.delivery.mention import mentioned_handles
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.living_doc import collab, work_edits
from app.domain.living_doc.comments import CommentThreads
from app.domain.living_doc.services import Documents

logger = logging.getLogger(__name__)

#: Replies written for the agent when it has no answer of its own.
BUSY = "{agent}正忙，暂时无法回复，稍后重试"
FAILED = "{agent}暂时无法回复，稍后重试"
#: What ends the reply of an answer someone stopped.
STOPPED = "已停止"


async def _tell(
    document_id: uuid.UUID, thread_id: uuid.UUID, state: str, tool: str | None = None
) -> None:
    """How far the thread's question has got, for whoever has the document
    open."""
    frame = {"type": "comment_activity", "thread": str(thread_id), "state": state}
    if tool:
        frame["tool"] = tool
    await collab.tell(document_id, frame)


# --- who is asked --------------------------------------------------------------


async def mentioned_seat(
    db: AsyncSession, asked: Asked, actor: Actor, content: str
) -> str | None:
    """The document's agent this comment names, when a person wrote it."""
    seats = await question.seats(db, asked)
    named = [handle for handle in mentioned_handles(content) if handle in seats]
    if not named or await IdentityService(db).is_agent(actor.handle):
        return None
    return named[0]


def hand_to_agent(
    chat: ChatService,
    consumptions: Consumptions,
    *,
    asked: Asked,
    actor: Actor,
    seat: str,
    thread_id: uuid.UUID,
) -> None:
    """Answer ``thread_id`` as ``seat``, for ``actor``. Call after the comment
    is committed: the answer reads the thread."""
    spawn(
        answer(
            chat,
            consumptions,
            asked=asked,
            asker=actor.handle,
            seat=seat,
            thread_id=thread_id,
        ),
        name=f"doc-agent-{thread_id}",
    )


async def thread_question(
    db: AsyncSession,
    around: Surroundings,
    *,
    document_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> str:
    """A thread's question: the thread so far, what it points at, the document
    and, for a task's document, the task's latest messages."""
    threads = CommentThreads(db)
    thread = await threads.describe(await threads.root(document_id, thread_id))
    comments = [thread["comment"], *(r["comment"] for r in thread["replies"])]
    said = "\n".join(f"<@{c['author']}>：{c['content']}" for c in comments)
    quote = thread["comment"].get("anchor_quote") or ""
    parts = [
        f"<评论串>\n{said}\n</评论串>",
        *around.around(quote, "评论指着的文字"),
        around.document(),
        *around.conversation(),
        f"回答评论串里 <@{comments[-1]['author']}> 的最后一条评论。",
    ]
    return "\n\n".join(parts)


# --- one answer ----------------------------------------------------------------


async def answer(
    chat: ChatService,
    consumptions: Consumptions,
    *,
    asked: Asked,
    asker: str,
    seat: str,
    thread_id: uuid.UUID,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Answer the thread's last comment as ``seat``, and reply with it: what
    can be refused is refused here, and the answer is read to its end by the
    questions the platform reads (``Replies``)."""
    factory = factory or chat.session_factory
    redis = get_redis_client()
    async with factory() as db:
        bound = await question.bind(db, asked, seat)
    if redis is None:
        await reply(factory, asked, thread_id, bound, FAILED)
        return
    work = uuid.uuid4()
    stopped = question.stopped(redis, thread_id)
    slot = await question.take_turn(
        redis,
        asked.project_id,
        thread_id,
        on_wait=lambda: _tell(asked.document_id, thread_id, "queued"),
        stopped=stopped,
    )
    if slot is None:
        said = STOPPED if await stopped() else BUSY
        # A stop is for the question it reached: the next one starts unstopped.
        await question.unstop(redis, thread_id)
        await reply(factory, asked, thread_id, bound, said)
        return
    await _tell(asked.document_id, thread_id, "working")
    try:
        async with factory() as db:
            await question.admit(db, asked.project_id, bound)
            around = await question.surroundings(db, asked, seat=bound.agent_handle)
            prompt = await thread_question(
                db, around, document_id=asked.document_id, thread_id=thread_id
            )
    except ValidationError as exc:
        # Refused before anything was asked: the refusal is the answer.
        await slot.release()
        await question.unstop(redis, thread_id)
        await reply(factory, asked, thread_id, bound, str(exc))
        return
    except BaseException:
        await slot.release()
        raise

    async def prompted() -> Prompt:
        # Minted once the session is there: its lifetime is the answer's.
        async with factory() as db:
            acting = await question.credential(
                db,
                asked=asked,
                agent=bound.agent_handle,
                asker=asker,
                work=work,
                may_edit=True,
            )
        return Prompt(work, prompt, acting=acting)

    await consumptions.begin(
        kind=KIND,
        key=str(thread_id),
        data={**asked.data(), "bound": asdict(bound)},
        work_id=work,
        session=session_for(
            asked=asked,
            key=thread_id,
            bound=bound,
            around=around,
            where="thread",
        ),
        prompt=prompted,
        ceiling_s=question.ANSWER_S,
        slots=[slot],
    )


#: A comment thread's question, to the questions the platform reads to the end
#: (``session_host.consumptions``); its key is the thread.
KIND = "doc-thread"


class Replies:
    """What becomes of a thread's answer: the document's readers hear how far
    it has got, the thread gets it as the agent's reply, and the project pays
    for it."""

    def __init__(self, chat: ChatService, factory: async_sessionmaker[AsyncSession]):
        self._chat = chat
        self._factory = factory

    async def stopped(self, consumption: Consumption) -> bool:
        redis = get_redis_client()
        return (
            redis is not None
            and await question.stopped(redis, uuid.UUID(consumption.key))()
        )

    async def took(
        self, consumption: Consumption, item: Waiting | Words | Tool
    ) -> None:
        document_id = Asked.of(consumption.data).document_id
        thread_id = uuid.UUID(consumption.key)
        if isinstance(item, Waiting):
            await _tell(document_id, thread_id, "queued")
        elif isinstance(item, Tool):
            await _tell(document_id, thread_id, "working", item.name)

    async def ended(
        self,
        consumption: Consumption,
        answer: Answer,
        *,
        written: str,
        stopped: bool,
        failure: BaseException | None,
    ) -> list[tuple[str, dict]]:
        asked = Asked.of(consumption.data)
        thread_id = uuid.UUID(consumption.key)
        bound = Bound(**consumption.data["bound"])
        text = answer.text.strip()
        if isinstance(failure, HostFull):
            said = BUSY
        elif isinstance(failure, StartAbandoned):
            said = ""
        elif failure is not None or answer.error or not text:
            logger.warning(
                "doc agent answer failed thread=%s: %s",
                thread_id,
                failure or answer.error,
            )
            said = FAILED
        else:
            said = text
        redis = get_redis_client()
        if redis is not None:
            await work_edits.take(redis, consumption.work_id)
        if stopped:
            # What it had written when it was stopped stays, marked as stopped.
            said = "\n\n".join(part for part in (written.strip(), STOPPED) if part)
        if redis is not None:
            await question.unstop(redis, thread_id)
        await reply(self._factory, asked, thread_id, bound, said)
        await self._chat.charge_turn_spend(
            asked.project_id, asked.task_id, consumption.work
        )
        return []


async def stop(
    redis: Redis, sessions: SessionHost, *, project_id: uuid.UUID, thread_id: uuid.UUID
) -> None:
    """Stop the thread's question: its wait, or the answer being written. The
    reply keeps what was written, marked as stopped."""
    await question.stop(redis, sessions, thread_id, ref(project_id, thread_id))


async def reply(
    factory: async_sessionmaker[AsyncSession],
    asked: Asked,
    thread_id: uuid.UUID,
    bound: Bound,
    text: str,
) -> None:
    """The agent's reply in the thread; ``{agent}`` in it is the agent's name.
    A thread that moved on in the meantime is read again once; one resolved or
    deleted meanwhile stays without it."""
    text = text.replace("{agent}", bound.agent_name)
    for attempt in range(2):
        async with factory() as db:
            threads = CommentThreads(db)
            try:
                doc = await Documents(db).get(asked.document_id)
                if doc is None:
                    raise NotFoundError("document gone")
                current = await threads.describe(await threads.root(doc.id, thread_id))
                await threads.mutate(
                    doc,
                    comment_id=thread_id,
                    author=bound.agent_handle,
                    expected_revision=current["revision"],
                    action="reply",
                    content=text,
                )
                await db.commit()
                await collab.tell(
                    asked.document_id, {"type": "state", "resource": "comments"}
                )
                return
            except NotFoundError:
                logger.info("doc agent reply dropped thread=%s: gone", thread_id)
                return
            except ConflictError:
                await db.rollback()
                if attempt:
                    logger.info("doc agent reply dropped thread=%s", thread_id)
