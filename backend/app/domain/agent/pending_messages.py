"""Resume addressed messages only after durable input ownership permits it."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.addressing import Event, Hand, address
from app.domain.agent.models import AgentTurn
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.models import Block, consumed_turn
from app.domain.block.queries import DEFERRED_INPUT, queued_messages
from app.domain.delivery.agent import redispatch_undelivered
from app.domain.delivery.input_holds import seats_with_unfinished_input
from app.domain.identity.handles import recipient_seat
from app.domain.notice_meta import EVENT_DELIVERY_FALLBACK, EVENT_TURN_QUEUED
from app.domain.run_record.models import RunRecord
from app.domain.thread.services import answered_in_many

_runner = None
logger = logging.getLogger(__name__)


def bind_runner(runner):
    global _runner
    _runner = runner


def current_runner():
    return _runner


def wake_held_deliveries(chat, conversation_id, recipient_handle):
    """Send, in the background, what the platform owes the seat that just freed.

    A delivery tried while its seat still held an earlier work went back to
    the ledger with a backoff (``delivery.agent.run_attempt``) — the same
    refusal that defers a person's message, which ``nudge_messages`` wakes on
    this same ending. Left to its backoff, it waited up to five minutes past
    the moment it was waiting for, and an agent working in long turns kept
    taking new work in that gap until the delivery was given up.
    """
    runner = _runner
    if runner is None or not runner.owns_sessions or not runner.accepting_turns:
        # The periodic dispatch still picks it up when its backoff runs out.
        return

    async def wake():
        try:
            await redispatch_undelivered(
                chat.session_factory,
                conversation_id=conversation_id,
                chat=chat,
                runner=runner,
                recipient_handle=recipient_handle,
            )
        except Exception:
            logger.exception(
                "held delivery wake failed topic=%s seat=%s",
                conversation_id,
                recipient_handle,
            )

    task = asyncio.create_task(wake(), name=f"held-deliveries:{conversation_id}")
    runner._tasks.add(task)
    task.add_done_callback(runner._tasks.discard)


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
    from app.domain.agent.queries import seats_in_conversations

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
        # Only messages this scan may start a turn for are asked where they are
        # answered: that read opens the 支线 of a main-line message, so it may
        # not run for one already begun or answered.
        waiting = [
            block
            for block in mentioned
            if block.id not in begun
            and block.id not in answered
            and not runner.turn_pending(block.id)
        ]
        # A channel's main line is answered in the message's 支线. The messages
        # of one scan come from a handful of conversations, so the question is
        # put once for the batch instead of twice per message
        # (`thread.services.answered_in_many`).
        answered_in_by_block = await answered_in_many(session, waiting)
        # The hold each message runs into is keyed by (conversation, seat), and
        # the seat is the one the message named: the loop checks `acting`
        # against exactly that before asking. One read answers every pair.
        holding = await seats_with_unfinished_input(
            session,
            [
                (
                    answered_in_by_block[block.id],
                    recipient_seat((block.meta or {}).get("agent_recipient") or {}),
                )
                for block in waiting
            ],
        )
        # The seat each waiting message acts under is asked once for the batch:
        # the messages of one scan come from a handful of conversations, so a
        # batch read answers them all instead of one `conversation_seat` per
        # message. The loop still checks the same things in the same order.
        seats_by_key = await seats_in_conversations(
            session,
            [
                (
                    answered_in_by_block[block.id],
                    ((block.meta or {}).get("agent_recipient") or {}).get("handle"),
                )
                for block in waiting
            ],
        )
        seats = {}
        for block in mentioned:
            if block.id not in answered_in_by_block:
                continue
            recipient = (block.meta or {}).get("agent_recipient") or {}
            handle = recipient.get("handle")
            conversation = answered_in_by_block[block.id]
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
            seated = seats_by_key[key]
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
            if (conversation, acting) in holding:
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
        if await chat.has_running_turn(room, handle):
            continue
        # No await between this final reservation check and scheduling. Other
        # scans may have selected the same row while this one awaited the DB.
        if not runner.owns_sessions or not runner.accepting_turns:
            break
        if runner.turn_pending(block.id):
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
