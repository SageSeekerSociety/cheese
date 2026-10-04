"""A document comment that names the room's agent is answered by the comment
thread's own session.

Comments are passive: recording one starts nothing. A comment, or a reply in a
comment thread, that @-mentions an agent seated in the room is a question to
that agent. It is answered in the background, after the comment is committed:

1. **Its turn.** One question of a thread is answered at a time, and at most
   ``question.ANSWERING_PER_PROJECT`` of a project's conversations are being
   answered at once; a question waits for its turn up to ``question.WAIT_S``.
2. **Admission.** The agent's model and the project's credits, as a turn of the
   agent would be admitted (``question.admit``).
3. **The question**, assembled fresh: the thread so far, the passage it is
   anchored to and the section around it, the whole document, the room's recent
   messages (``thread_question``).
4. **The answer** becomes the agent's reply in the thread (``reply``).

The room hears how far a thread's question has got as it goes
(``comment_activity`` frames: waiting for a session, answering, which tool it
used), and the thread list says it again for a page that opens meanwhile
(``question.answering``).

What the answer spent is drained into the project's usage once it is done,
answered or not: it was spent either way.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.background import spawn
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.redis import get_redis_client
from app.domain.agent.chat import ChatService
from app.domain.agent.document import question
from app.domain.agent.document.question import Bound, Surroundings
from app.domain.agent.document.session import session_for
from app.domain.agent.runtime import announce_stale, get_broker
from app.domain.agent.session_host.answer import Answer, Tool, ask
from app.domain.agent.session_host.contract import HostFull, Prompt, SessionError
from app.domain.agent.session_host.host import SessionHost
from app.domain.block.comment_threads import CommentThreads
from app.domain.delivery.mention import mentioned_handles
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.living_doc import work_edits
from app.domain.room_task.place import Place
from app.domain.topic_membership.services import TopicMemberService

logger = logging.getLogger(__name__)

#: Replies written for the agent when it has no answer of its own.
BUSY = "{agent}正忙，暂时无法回复，稍后重试"
FAILED = "{agent}暂时无法回复，稍后重试"


async def _tell(
    room_id: uuid.UUID, thread_id: uuid.UUID, state: str, tool: str | None = None
) -> None:
    """How far the thread's question has got, for the room's open pages."""
    frame = {"type": "comment_activity", "thread": str(thread_id), "state": state}
    if tool:
        frame["tool"] = tool
    await get_broker().publish(str(room_id), frame)


# --- who is asked --------------------------------------------------------------


async def mentioned_seat(
    db: AsyncSession, place: Place, actor: Actor, content: str
) -> str | None:
    """The room's agent this comment names, when a person wrote it."""
    seats = await TopicMemberService(db).agent_handles(place.room_id)
    named = [handle for handle in mentioned_handles(content) if handle in seats]
    if not named or await IdentityService(db).is_agent(actor.handle):
        return None
    return named[0]


def hand_to_agent(
    chat: ChatService,
    sessions: SessionHost,
    *,
    place: Place,
    actor: Actor,
    seat: str,
    thread_id: uuid.UUID,
) -> None:
    """Answer ``thread_id`` as ``seat``, for ``actor``. Call after the comment
    is committed: the answer reads the thread."""
    spawn(
        answer(
            chat,
            sessions,
            project_id=place.project_id,
            room_id=place.room_id,
            asker=actor.handle,
            seat=seat,
            thread_id=thread_id,
        ),
        name=f"doc-agent-{thread_id}",
    )


async def thread_question(
    db: AsyncSession, around: Surroundings, *, room_id: uuid.UUID, thread_id: uuid.UUID
) -> str:
    """A thread's question: the thread so far, what it points at, the document
    and the room's latest messages."""
    threads = CommentThreads(db)
    thread = await threads.describe(await threads.root(room_id, thread_id))
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
    sessions: SessionHost,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    asker: str,
    seat: str,
    thread_id: uuid.UUID,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Answer the thread's last comment as ``seat``, and reply with it."""
    factory = factory or chat.session_factory
    redis = get_redis_client()
    async with factory() as db:
        bound = await question.bind(db, room_id, seat)
    if redis is None:
        await reply(factory, room_id, project_id, thread_id, bound, FAILED)
        return
    work = uuid.uuid4()
    slot = await question.take_turn(
        redis,
        project_id,
        thread_id,
        on_wait=lambda: _tell(room_id, thread_id, "queued"),
    )
    if slot is None:
        await reply(factory, room_id, project_id, thread_id, bound, BUSY)
        return
    await _tell(room_id, thread_id, "working")
    spent = False
    try:
        async with factory() as db:
            await question.admit(db, project_id, bound)
            around = await question.surroundings(
                db, project_id=project_id, room_id=room_id, seat=bound.agent_handle
            )
            prompt = await thread_question(
                db, around, room_id=room_id, thread_id=thread_id
            )
            acting = await question.credential(
                db,
                project_id=project_id,
                room_id=room_id,
                agent=bound.agent_handle,
                asker=asker,
                work=work,
                may_edit=True,
            )
        started = session_for(
            project_id=project_id,
            room_id=room_id,
            key=thread_id,
            bound=bound,
            around=around,
            where="thread",
        )
        text, failure = "", None
        spent = True
        async for event in ask(
            sessions,
            *started,
            Prompt(work, prompt, acting=acting),
            work_id=work,
            ceiling_s=question.ANSWER_S,
        ):
            if isinstance(event, Tool):
                await _tell(room_id, thread_id, "working", event.name)
            elif isinstance(event, Answer):
                text, failure = event.text, event.error
        if failure:
            logger.warning("doc agent answer failed thread=%s: %s", thread_id, failure)
        said = text.strip() if text.strip() and not failure else FAILED
    except ValidationError as exc:
        # Refused before anything was asked: the refusal is the answer.
        said = str(exc)
    except HostFull:
        said = BUSY
    except SessionError as exc:
        logger.warning("doc agent session failed thread=%s: %s", thread_id, exc)
        said = FAILED
    except Exception:  # noqa: BLE001 — the thread is told; the log keeps why
        logger.warning("doc agent answer failed thread=%s", thread_id, exc_info=True)
        said = FAILED
    finally:
        await slot.release()
        await work_edits.take(redis, str(work))
    await reply(factory, room_id, project_id, thread_id, bound, said)
    if spent:
        await chat.charge_turn_spend(project_id, room_id, work)


async def reply(
    factory: async_sessionmaker[AsyncSession],
    room_id: uuid.UUID,
    project_id: uuid.UUID,
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
                current = await threads.describe(await threads.root(room_id, thread_id))
                await threads.mutate(
                    room_id=room_id,
                    project_id=project_id,
                    comment_id=thread_id,
                    author=bound.agent_handle,
                    expected_revision=current["revision"],
                    action="reply",
                    content=text,
                )
                await db.commit()
                await announce_stale(room_id, "comments")
                return
            except NotFoundError:
                logger.info("doc agent reply dropped thread=%s: gone", thread_id)
                return
            except ConflictError:
                await db.rollback()
                if attempt:
                    logger.info("doc agent reply dropped thread=%s", thread_id)
