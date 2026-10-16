"""An instruction waiting behind its agent's busy seat waits for it, not for a clock.

A seat runs one turn at a time, and one turn can run for hours. An instruction
that arrives meanwhile is refused while the seat holds that earlier work, and
used to be given up half an hour after it was given like one whose computer is
gone: on dev, 369 of the 420 given up in the 15 days to 2026-10-16 had their
seat still holding an earlier input at that moment.

The rules, as stated before the code was written:

- an instruction refused only because its seat is busy is still owed past half
  an hour, and goes in when the work it waited for ends;
- nobody is paged about it while it waits;
- it does not wait forever: a day after it was given it is given up.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.api.deps import get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.models import Delivery
from app.domain.delivery.overdue import report_overdue
from app.domain.delivery.timer import give_up_stale
from app.domain.identity.handles import agent_instance_handle
from tests.conftest import stub_compute
from tests.integration.test_a_freed_seat_takes_the_delivery_it_held_back import (
    Seat,
    _instruction_for,
    _room_with_agent,
    _row,
)
from tests.support.hang import HANG_S


async def _given_ago(factory, delivery_id, ago: timedelta) -> None:
    """Time passes: the instruction was given ``ago``, and its backoff is over."""
    async with factory() as session:
        row = await session.get(Delivery, delivery_id)
        row.recorded_at = row.event_at = datetime.now(UTC) - ago
        row.retry_at = None
        await session.commit()


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

        # The turn goes on for well over half an hour. The instruction is
        # tried again meanwhile, and the seat is still busy.
        await _given_ago(factory, delivery_id, timedelta(minutes=50))
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
        delivery_id = await _instruction_for(
            factory, conversation, instance_id, "the checks on PR 12 failed"
        )
        await _given_ago(factory, delivery_id, timedelta(hours=25))
        await _try_once(factory, chat, runner, delivery_id)

        await give_up_stale(factory, chat=chat)

        row = await _row(factory, delivery_id)
        assert row.state == "failed" and row.sent_at is None
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)
