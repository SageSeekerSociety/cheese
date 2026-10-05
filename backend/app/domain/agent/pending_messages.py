"""Resume addressed messages only after durable input ownership permits it."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from app.domain.agent.models import AgentTurn
from app.domain.agent.platform_notices import EVENT_DELIVERY_FALLBACK, EVENT_TURN_QUEUED
from app.domain.block.models import (
    CONSUMED_TURN_META_KEY,
    Block,
    BlockKind,
    consumed_turn,
    prompt_attempts,
)
from app.domain.delivery.addressing import Event, Hand, address
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.identity.handles import recipient_seat

DEFERRED_INPUT = "deferred_native_input"
_runner = None


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
    nudge_messages(chat, completion.topic_id)


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
    nudge_messages(chat, termination.topic_id)


async def defer_message(session, block_id):
    block = await session.get(Block, block_id, with_for_update=True)
    if block is not None and consumed_turn(block) is None:
        block.meta = {**(block.meta or {}), DEFERRED_INPUT: True}


def nudge_messages(chat, topic_id):
    runner = _runner
    caller = asyncio.current_task()
    if (
        runner is None
        or not runner.owns_sessions
        or not runner.accepting_turns
        or (caller is not None and caller.cancelling())
    ):
        return

    # Handover waits/cancels these scans with the runner's other work.
    async def scan():
        try:
            await runner.resume_lost_messages(chat, topic_id=topic_id)
        except Exception:
            logging.getLogger(__name__).exception(
                "pending message recovery failed topic=%s", topic_id
            )

    task = asyncio.create_task(scan(), name=f"pending-messages:{topic_id}")
    runner._tasks.add(task)
    task.add_done_callback(runner._tasks.discard)


async def resume_messages(runner, chat, *, topic_id=None):
    from app.domain.agent.queries import session_agent_in_room

    if not runner.owns_sessions or not runner.accepting_turns:
        return 0
    since = datetime.now(UTC) - timedelta(seconds=runner.ORPHAN_STALE_S)
    async with chat.session_factory() as session:
        query = select(Block).where(
            or_(
                Block.created_at >= since,
                Block.meta[DEFERRED_INPUT].as_boolean().is_(True),
            ),
            Block.kind == BlockKind.message,
            Block.meta["agent_recipient"]["mentioned"].as_boolean(),
        )
        if topic_id is not None:
            query = query.where(Block.topic_id == topic_id)
        mentioned = [
            block
            for block in await session.scalars(
                query.order_by(Block.created_at, Block.id)
            )
            if CONSUMED_TURN_META_KEY in (block.meta or {})
            and consumed_turn(block) is None
            and prompt_attempts(block) == 0
            and "delivery_event_id" not in (block.meta or {})
        ]
        if not mentioned:
            return 0
        ids = [block.id for block in mentioned]
        begun = set(
            await session.scalars(select(AgentTurn.id).where(AgentTurn.id.in_(ids)))
        )
        busy = {}
        for room, handle in await session.execute(
            select(AgentTurn.topic_id, AgentTurn.agent_handle).where(
                AgentTurn.stopped_at.is_(None)
            )
        ):
            busy.setdefault(room, set()).add(handle)
        answered = {
            block.turn_id
            for block in await session.scalars(
                select(Block).where(Block.turn_id.in_(ids), Block.id.not_in(ids))
            )
            if (block.meta or {}).get("event_type")
            not in {EVENT_TURN_QUEUED, EVENT_DELIVERY_FALLBACK}
        }
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
            key = (block.topic_id, handle)
            if key in seats:
                continue
            running = busy.get(block.topic_id, set())
            if None in running or handle in running or chat.has_running_turn(*key):
                continue
            agent = await session_agent_in_room(session, block.topic_id, handle)
            if agent is None:
                continue
            if agent.handle != handle:
                continue
            if recipient.get("instance_id") not in (None, str(agent.instance_id)):
                continue
            acting = await chat._acting_handle(session, block.topic_id, agent)
            if acting != recipient_seat(recipient):
                continue
            if await seat_has_unfinished_input(session, block.topic_id, acting):
                continue
            seats[key] = block
    started = 0
    for (room, handle), block in seats.items():
        # No await between this final reservation check and scheduling. Other
        # scans may have selected the same row while this one awaited the DB.
        if not runner.owns_sessions or not runner.accepting_turns:
            break
        if runner.turn_pending(block.id) or chat.has_running_turn(room, handle):
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
            provision_actor=None,
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
    return started
