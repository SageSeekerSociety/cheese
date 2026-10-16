"""An instruction waiting behind its agent's busy seat waits for it, not for a clock.

A seat runs one turn at a time, and one turn can run for hours. An instruction
that arrives meanwhile is refused while the seat holds that earlier work, and
used to be given up half an hour after it was given like one whose computer is
gone: on dev, 369 of the 420 given up in the 15 days to 2026-10-16 had their
seat still holding an earlier input at that moment.

The rules, as stated before the code was written:

- an instruction refused only because its seat is busy — a turn there is still
  running — is still owed past half an hour, and goes in when that work ends;
- a seat held by a turn that is not running — it has ended, or it has said
  nothing for the orphan sweep's silence limit — is not busy: the
  instruction is paged about and given up half an hour on, as before;
- nobody is paged about it while it waits;
- it does not wait forever: a day after it was given it is given up, and a
  person waiting on it is told so, not told it waited half an hour.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.api.deps import get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.overdue import report_overdue
from app.domain.delivery.timer import give_up_stale
from app.domain.identity.handles import agent_instance_handle
from app.domain.notification.models import NotificationType
from tests.conftest import stub_compute
from tests.integration.test_a_freed_seat_takes_the_delivery_it_held_back import (
    Seat,
    _instruction_for,
    _room_with_agent,
    _row,
)
from tests.support.hang import HANG_S


async def _time_passes(factory, delivery_id, by: timedelta) -> None:
    """``by`` passes for the instruction: every moment on it, from when it was
    given to its last attempt, is that much further back, and its backoff is
    over."""
    async with factory() as session:
        row = await session.get(Delivery, delivery_id)
        row.recorded_at -= by
        row.event_at -= by
        if row.seat_busy_at is not None:
            row.seat_busy_at -= by
        row.retry_at = None
        await session.commit()


async def _turn_ages(factory, conversation, by: timedelta) -> None:
    """``by`` passes for the turn running in ``conversation``: it started that
    much earlier, and everything it said was said that much earlier."""
    async with factory() as session:
        await session.execute(
            update(AgentTurn)
            .where(AgentTurn.conversation_id == conversation)
            .values(started_at=AgentTurn.started_at - by)
        )
        await session.execute(
            update(Block)
            .where(Block.conversation_id == conversation)
            .values(created_at=Block.created_at - by)
        )
        await session.commit()


async def _agent_works(factory, screen, conversation, actor) -> None:
    """The agent runs a command in its turn, and the room has it."""
    stamp = datetime.now(UTC)
    screen.uses(conversation, "Bash", agent=actor, command="pytest")
    async with asyncio.timeout(HANG_S):
        while not await _said_since(factory, conversation, stamp):
            await asyncio.sleep(0.05)


async def _said_since(factory, conversation, stamp) -> bool:
    async with factory() as session:
        return bool(
            await session.scalar(
                select(Block.id).where(
                    Block.conversation_id == conversation,
                    Block.turn_id.is_not(None),
                    Block.created_at >= stamp,
                )
            )
        )


async def _read_by_session(factory, conversation) -> bool:
    async with factory() as session:
        return bool(
            await session.scalar(
                select(NativeInput.id).where(
                    NativeInput.conversation_id == conversation,
                    NativeInput.echoed_at.is_not(None),
                )
            )
        )


async def _room_lines(factory, conversation) -> list[Block]:
    async with factory() as session:
        return list(
            await session.scalars(
                select(Block).where(
                    Block.conversation_id == conversation,
                    Block.meta["event_type"].as_string() == "turn_failed",
                )
            )
        )


async def _busy(chat, runner, screen, conversation, seat) -> None:
    """The agent takes on a person's request and is still working on it."""
    await runner.receive_message(
        chat, conversation, author="u", content=f"<@{seat}> a long piece of work"
    )
    await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
    screen.handed_one.clear()


async def _try_once(factory, chat, runner, delivery_id) -> Delivery:
    assert (
        await dispatch_pending(
            factory, chat=chat, runner=runner, delivery_ids=[delivery_id]
        )
        == 1
    ), "the instruction was not tried"
    async with asyncio.timeout(HANG_S):
        while (row := await _row(factory, delivery_id)).state != "pending":
            await asyncio.sleep(0.05)
    assert row.sent_at is None, "the seat was busy; nothing should have gone in"
    return row


def _chat(factory, screen, runner, tmp_path):
    return ChatService(
        work_runner=runner,
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )


@pytest.mark.anyio
async def test_an_instruction_behind_a_long_turn_goes_in_when_it_ends(
    business_db_factory, tmp_path
):
    factory = business_db_factory
    conversation, seat, instance_id = await _room_with_agent(factory)
    actor = agent_instance_handle(instance_id)
    screen = Seat()
    runner = get_work_runner()
    chat = _chat(factory, screen, runner, tmp_path)
    try:
        await _busy(chat, runner, screen, conversation, seat)
        delivery_id = await _instruction_for(
            factory, conversation, instance_id, "the checks on PR 12 failed"
        )
        await _try_once(factory, chat, runner, delivery_id)
        paged_before = await report_overdue(factory)

        # The turn goes on for well over half an hour, working all the while.
        # The instruction is tried again meanwhile, and each time the seat is
        # still busy: its half hour counts from the last of those, not the
        # first.
        for _ in range(3):
            await _time_passes(factory, delivery_id, timedelta(minutes=25))
            await _turn_ages(factory, conversation, timedelta(minutes=25))
            await _agent_works(factory, screen, conversation, actor)
            await _try_once(factory, chat, runner, delivery_id)

        # Nobody is paged about it, and the give-up sweep leaves it owed.
        assert await report_overdue(factory) == paged_before
        await give_up_stale(factory, chat=chat)
        row = await _row(factory, delivery_id)
        assert row.state == "pending", (
            f"given up while it waited for a busy seat: {row.last_error}"
        )

        # The work ends; the instruction goes in.
        screen.says(conversation, "done", agent=actor)
        screen.stops(conversation, "done", agent=actor)
        await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
        assert "the checks on PR 12 failed" in screen.handed[-1]
        async with asyncio.timeout(HANG_S):
            while (row := await _row(factory, delivery_id)).sent_at is None:
                await asyncio.sleep(0.05)
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_an_instruction_still_behind_a_busy_seat_a_day_later_is_given_up(
    business_db_factory, tmp_path
):
    factory = business_db_factory
    conversation, seat, instance_id = await _room_with_agent(factory)
    screen = Seat()
    runner = get_work_runner()
    chat = _chat(factory, screen, runner, tmp_path)
    try:
        await _busy(chat, runner, screen, conversation, seat)
        # A person's message to it, given a day ago.
        delivery_id = await _instruction_for(
            factory, conversation, instance_id, f"<@{seat}> take a look"
        )
        async with factory() as session:
            row = await session.get(Delivery, delivery_id)
            row.type = NotificationType.MENTION.value
            await session.commit()
        await _time_passes(factory, delivery_id, timedelta(hours=25))
        await _try_once(factory, chat, runner, delivery_id)

        await give_up_stale(factory, chat=chat)

        row = await _row(factory, delivery_id)
        assert row.state == "failed" and row.sent_at is None
        # The person is told, once, and not that it waited half an hour.
        [line] = await _room_lines(factory, conversation)
        assert (line.meta or {}).get("retryable") is True
        said = re.sub(r"<@[\w-]+>", "", line.content or "")
        assert "30" not in said, line.content
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
@pytest.mark.parametrize("how", ["ended", "gone quiet"])
async def test_a_seat_held_by_a_turn_that_is_not_running_is_not_busy(
    business_db_factory, tmp_path, how
):
    """The agent's session went out of reach mid-turn. Either the platform
    ended the turn, or it could not tell the session was gone and the turn
    stays open, silent for hours. Either way the input it had read still holds
    the seat and nothing will release it. An instruction refused by that hold
    is not waiting for any work to finish; it is paged about and given up half
    an hour on."""
    factory = business_db_factory
    conversation, seat, instance_id = await _room_with_agent(factory)
    screen = Seat()
    runner = get_work_runner()
    chat = _chat(factory, screen, runner, tmp_path)
    try:
        await _busy(chat, runner, screen, conversation, seat)
        async with asyncio.timeout(HANG_S):
            while not await _read_by_session(factory, conversation):
                await asyncio.sleep(0.05)
        async with factory() as session:
            if how == "ended":
                # Its interval is closed; the input is left holding the seat.
                await session.execute(
                    update(AgentTurn)
                    .where(
                        AgentTurn.conversation_id == conversation,
                        AgentTurn.stopped_at.is_(None),
                    )
                    .values(stopped_at=datetime.now(UTC))
                )
            else:
                # Still open, and nothing has come of it for two hours.
                hours_ago = datetime.now(UTC) - timedelta(hours=2)
                await session.execute(
                    update(AgentTurn)
                    .where(AgentTurn.conversation_id == conversation)
                    .values(started_at=hours_ago)
                )
                await session.execute(
                    update(Block)
                    .where(Block.conversation_id == conversation)
                    .values(created_at=hours_ago)
                )
            await session.commit()
        delivery_id = await _instruction_for(
            factory, conversation, instance_id, "the checks on PR 12 failed"
        )
        await _try_once(factory, chat, runner, delivery_id)
        paged_before = await report_overdue(factory)
        await _time_passes(factory, delivery_id, timedelta(minutes=20))
        await _try_once(factory, chat, runner, delivery_id)
        await _time_passes(factory, delivery_id, timedelta(minutes=15))

        assert await report_overdue(factory) == paged_before + 1
        await give_up_stale(factory, chat=chat)
        assert (await _row(factory, delivery_id)).state == "failed"
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)
