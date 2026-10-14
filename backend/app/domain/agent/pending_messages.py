"""Resume addressed messages only after durable input ownership permits it."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.agent.models import AgentTurn
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.models import Block, consumed_turn
from app.domain.block.queries import DEFERRED_INPUT, queued_messages
from app.domain.delivery.addressing import Event, Hand, address
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.identity.handles import recipient_seat
from app.domain.notice_meta import EVENT_DELIVERY_FALLBACK, EVENT_TURN_QUEUED
from app.domain.run_record.models import RunRecord
from app.domain.thread.services import answered_in

_runner = None
logger = logging.getLogger(__name__)


def bind_runner(runner):
    global _runner
    _runner = runner


def current_runner():
    return _runner


async def finish_work(chat, completion, settle):
    """Only a committed native completion can wake a deferred message."""
    async with chat.session_factory() as session:
        await settle(
            session,
            require_registered=True,
            **{
                field: getattr(completion, field)
                for field in completion.__dataclass_fields__
            },
        )
        await session.commit()
    nudge_messages(chat, completion.conversation_id)


async def finish_work_termination(chat, termination, settle):
    """A work interval proved dead frees the seat, and nothing else.

    It does not complete the inputs inside it: whether an answer was taken is
    still unknown, so their holds stay. What it does is stop those rows from
    standing between the seat and the next input — and the deferred messages
    have to be rescanned, because that is what was holding them back.
    """
    async with chat.session_factory() as session:
        await settle(
            session,
            **{
                field: getattr(termination, field)
                for field in termination.__dataclass_fields__
            },
        )
        await session.commit()
    nudge_messages(chat, termination.conversation_id)


async def defer_message(session, block_id):
    block = await session.get(Block, block_id, with_for_update=True)
    if block is not None and consumed_turn(block) is None:
        block.meta = {**(block.meta or {}), DEFERRED_INPUT: True}


def nudge_messages(chat, topic_id, runner=None):
    """Look for messages left waiting in ``topic_id``, in the background.

    ``runner`` is the runner the scan belongs to: it is tracked in that runner's
    tasks, so draining or handing over that runner waits for it. A runner ending
    one of its own turns passes itself; everything else uses the runner bound
    for incoming messages."""
    runner = runner or _runner
    caller = asyncio.current_task()
    if (
        runner is None
        or not runner.owns_sessions
        or not runner.accepting_turns
        or (caller is not None and caller.cancelling())
    ):
        # A nudge that goes nowhere leaves a waiting message to the sweep; this
        # line is what tells which of these it was when one waited too long.
        reason = (
            "no_runner"
            if runner is None
            else "not_owner"
            if not runner.owns_sessions
            else "not_accepting"
            if not runner.accepting_turns
            else "caller_cancelling"
        )
        logger.info(
            "pending_messages nudge_dropped topic=%s reason=%s", topic_id, reason
        )
        return

    # Handover waits/cancels these scans with the runner's other work.
    async def scan():
        try:
            await runner.resume_lost_messages(chat, topic_id=topic_id)
        except Exception:
            logger.exception("pending message recovery failed topic=%s", topic_id)

    task = asyncio.create_task(scan(), name=f"pending-messages:{topic_id}")
    runner._tasks.add(task)
    task.add_done_callback(runner._tasks.discard)


async def resume_messages(runner, chat, *, topic_id=None, source="nudge"):
    """``topic_id`` is a conversation: a room, a task or a 支线, whose
    messages are the blocks carrying its id.

    ``source`` says who asked: a ``nudge`` after something in the room changed,
    the periodic ``sweep``, or ``startup``. A nudge logs every waiting message it
    passes over and why; the sweep logs every message it starts, since one it
    finds is one the nudges missed."""
    # deferred-import: breaks the cycle agent.queries -> agent.pending_messages
    from app.domain.agent.queries import conversation_seat

    if not runner.owns_sessions or not runner.accepting_turns:
        return 0
    now = datetime.now(UTC)
    since = now - timedelta(seconds=runner.ORPHAN_STALE_S)
    skipped: list[tuple[Block, str]] = []
    async with chat.session_factory() as session:
        mentioned = list(await session.scalars(queued_messages(since, topic_id)))
        if not mentioned:
            return 0
        ids = [block.id for block in mentioned]
        begun = set(
            await session.scalars(select(AgentTurn.id).where(AgentTurn.id.in_(ids)))
        )
        # 一趟读全项目的开着的轮次：这个扫描是跨进程的，判据只能是库里那一行，
        # 不是本进程的内存缓存。这一读法与 `ChatService.has_running_turn` 共用同一
        # 个 `AgentTurnRepository` 方法，别处不许再抄一份近似查询。
        busy = await AgentTurnRepository(session).open_turn_seats()
        # Anything the turn left behind says it began, in the conversation or
        # in its run records, but a queue it is waiting in.
        answered = {
            block.turn_id
            for block in await session.scalars(
                select(Block).where(Block.turn_id.in_(ids), Block.id.not_in(ids))
            )
            if (block.meta or {}).get("event_type")
            not in {EVENT_TURN_QUEUED, EVENT_DELIVERY_FALLBACK}
        } | set(
            await session.scalars(
                select(RunRecord.turn_id).where(
                    RunRecord.turn_id.in_(ids),
                    RunRecord.kind.not_in((EVENT_TURN_QUEUED, EVENT_DELIVERY_FALLBACK)),
                )
            )
        )
        seats = {}
        for block in mentioned:
            if (
                block.id in begun
                or block.id in answered
                or runner.turn_pending(block.id)
            ):
                continue
            recipient = (block.meta or {}).get("agent_recipient") or {}
            handle = recipient.get("handle")
            # A channel's main line is answered in the message's 支线.
            conversation = await answered_in(session, block)
            key = (conversation, handle)
            if key in seats:
                continue
            running = busy.get(conversation, set())
            if None in running or handle in running:
                skipped.append((block, "turn_open_in_db"))
                continue
            if await chat.has_running_turn(*key):
                skipped.append((block, "turn_running_in_process"))
                continue
            seated = await conversation_seat(session, conversation, handle)
            if seated is None:
                skipped.append((block, "no_seat"))
                continue
            agent, acting = seated
            if agent.handle != handle:
                skipped.append((block, "seat_has_other_agent"))
                continue
            if recipient.get("instance_id") not in (None, str(agent.instance_id)):
                skipped.append((block, "other_instance"))
                continue
            if acting != recipient_seat(recipient):
                skipped.append((block, "other_acting_seat"))
                continue
            if await seat_has_unfinished_input(session, conversation, acting):
                skipped.append((block, "unfinished_input"))
                continue
            seats[key] = block
        await session.commit()
    if source != "sweep":
        for block, reason in skipped:
            logger.info(
                "pending_messages skipped source=%s topic=%s block=%s reason=%s"
                " waited_s=%.0f",
                source,
                block.conversation_id,
                block.id,
                reason,
                (now - block.created_at).total_seconds(),
            )
    started = 0
    for (room, handle), block in seats.items():
        # No await between this final reservation check and scheduling. Other
        # scans may have selected the same row while this one awaited the DB.
        if not runner.owns_sessions or not runner.accepting_turns:
            break
        if runner.turn_pending(block.id) or await chat.has_running_turn(room, handle):
            continue
        recipient = (block.meta or {}).get("agent_recipient") or {}
        runner._receive_message(
            chat,
            room,
            block.id,
            addressed=address(Event(asked=recipient_seat(recipient)), Hand.participant),
            continuation_id=block.id,
            author=block.author,
            content=block.content,
            reply_to=str(block.reply_to) if block.reply_to else None,
            attachments=None,
            landed_user_block_id=block.id,
            landed_user_block_ids=[block.id],
            live_delivery_expected=False,
            recipient_handle=recipient.get("handle"),
            recipient_instance_id=(
                uuid.UUID(recipient["instance_id"])
                if recipient.get("instance_id")
                else None
            ),
        )
        started += 1
        logger.info(
            "pending_messages started source=%s topic=%s block=%s waited_s=%.0f",
            source,
            room,
            block.id,
            (now - block.created_at).total_seconds(),
        )
    return started
