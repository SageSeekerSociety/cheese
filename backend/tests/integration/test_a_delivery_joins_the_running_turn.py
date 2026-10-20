"""A delivery for an agent that is busy joins the turn it is running.

A seat runs one turn at a time. A ledger delivery — a PR's CI result, a timed
delivery, the second agent named in a message — used to wait for a turn of its
own, refused for as long as the seat ran another; behind a long turn it was
given up after half an hour (369 of 420 on dev in the 15 days to 2026-10-16).
A person's message, meanwhile, was always handed to the running turn.

The rules, as stated before the code was written:

- a delivery for a seat running a turn in that conversation is handed to that
  turn, and counts as delivered once the session has read it;
- a platform instruction does not move the turn's foreground work aside; a
  person naming the agent does, as their own message would;
- a finished task's write-up, a routine's run and a task's opening are not
  merged into the running turn: each goes to the session at once as an input
  of its own, under its own work, and the session reads it at its next tool
  boundary — a session that is working takes what it is given;
- a delivery the running turn did not take is not lost: it goes in as an
  input of its own.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api.deps import get_work_runner
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent.chat import ChatService
from app.domain.agent.opening import OPENING, PURPOSE
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.mention import BY_PERSON
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.identity.handles import agent_instance_handle
from app.domain.notice_meta import EVENT_ROUTINE_RUN
from app.domain.notification.models import NotificationType
from app.domain.room_task.closing import CLOSES_TASK
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import project_service, registered
from tests.support.threads import thread_in
from tests.support.hang import HANG_S


class Seat(StubChannel):
    """Records each input the agent's session is handed."""

    def __init__(self):
        super().__init__()
        self.handed: list[str] = []
        self.handed_one = asyncio.Event()

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        self.handed.append(prompt)
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.handed_one.set()


async def _room_with_agent(factory):
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        seat = (await TopicMemberService(session).agent_handles(room.id))[0]
        agent = await AgentInstanceService(session).for_seat_handle(project, seat)
        conversation = await thread_in(session, room)
        await session.commit()
    return conversation, seat, agent.instance_id


async def _row(factory, delivery_id):
    async with factory() as session:
        return await session.scalar(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .execution_options(populate_existing=True)
        )


class Working(Seat):
    """A session that takes its first input as a turn and keeps working on it;
    whatever it is handed after that it reads at its next tool boundary."""

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        if not self.handed:
            super().emit_turn(topic_id, prompt, reply, agent=agent)
            return
        self.handed.append(prompt)
        self.acknowledges(topic_id, prompt, agent=agent)
        self.handed_one.set()

    def moved_aside(self, topic_id) -> int:
        """How many times the session was told to move its foreground work to
        the background (Ctrl+B)."""
        return sum(
            1
            for session in self.sessions.values()
            if session.topic_id == topic_id
            for message in session.written
            if message.get("type") == "control_request"
            and message["request"].get("subtype") == "background_tasks"
        )


async def _delivery(factory, conversation, instance_id, content, **kind):
    """A delivery for the agent, as the ledger records one: a platform notice
    unless ``kind`` says otherwise."""
    payload = {"content": content, **kind.pop("payload", {})}
    async with factory() as session:
        stamp = datetime.now(UTC)
        row = Delivery(
            event_id=uuid.uuid4(),
            recipient_handle=agent_instance_handle(instance_id),
            agent_instance_id=instance_id,
            conversation_id=conversation,
            dedup_key=f"{uuid.uuid4()}:joins",
            type=kind.pop("type", NotificationType.ROOM_NOTICE.value),
            payload=payload,
            event_at=stamp,
            recorded_at=stamp,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _settled(factory, delivery_id, states) -> Delivery:
    async with asyncio.timeout(HANG_S):
        while (row := await _row(factory, delivery_id)).state not in states:
            await asyncio.sleep(0.05)
    return row


class _World:
    def __init__(self, factory, tmp_path):
        self.factory = factory
        self.screen = Working()
        self.runner = get_work_runner()
        self.chat = ChatService(
            work_runner=self.runner,
            session_factory=factory,
            compute=stub_compute(self.screen),
            base_system_prompt="You are Cheese.",
            workspace_root=str(tmp_path / "ws"),
        )

    async def busy(self):
        """A person gives the agent a long piece of work, and it is on it."""
        self.conversation, seat, self.instance_id = await _room_with_agent(self.factory)
        await self.runner.receive_message(
            self.chat,
            self.conversation,
            author="u",
            content=f"<@{seat}> a long piece of work",
        )
        await asyncio.wait_for(self.screen.handed_one.wait(), HANG_S)
        self.screen.handed_one.clear()
        # On it means it read the work: until its runner's report of the read
        # lands, the seat holds an input whose fate is unknown, and a delivery
        # tried then is (rightly) put back and retried later.
        async with asyncio.timeout(HANG_S):
            while True:
                async with self.factory() as session:
                    if not await seat_has_unfinished_input(
                        session, self.conversation, seat
                    ):
                        break
                await asyncio.sleep(0.02)

    async def dispatch(self, delivery_id) -> int:
        return await dispatch_pending(
            self.factory, chat=self.chat, runner=self.runner, delivery_ids=[delivery_id]
        )

    async def close(self):
        await self.runner.drain(timeout_s=10)
        await self.chat.stop_listening(timeout_s=2)


@pytest.fixture
async def world(business_db_factory, tmp_path):
    world = _World(business_db_factory, tmp_path)
    try:
        yield world
    finally:
        await world.close()


@pytest.mark.anyio
async def test_a_notice_for_a_busy_agent_is_read_inside_its_running_turn(world):
    await world.busy()
    delivery_id = await _delivery(
        world.factory,
        world.conversation,
        world.instance_id,
        "the checks on PR 12 failed",
    )

    assert await world.dispatch(delivery_id) == 1

    await asyncio.wait_for(world.screen.handed_one.wait(), HANG_S)
    assert "the checks on PR 12 failed" in world.screen.handed[-1]
    row = await _settled(world.factory, delivery_id, {"received"})
    assert row.sent_at is not None
    # Read where the turn was; the long work was not pushed aside for it.
    assert world.screen.moved_aside(world.conversation) == 0


@pytest.mark.anyio
async def test_a_person_naming_a_busy_agent_moves_its_work_aside(world):
    await world.busy()
    delivery_id = await _delivery(
        world.factory,
        world.conversation,
        world.instance_id,
        "u named you: switch to the other plan",
        type=NotificationType.MENTION.value,
        payload={"eventType": BY_PERSON},
    )

    assert await world.dispatch(delivery_id) == 1

    await asyncio.wait_for(world.screen.handed_one.wait(), HANG_S)
    assert "switch to the other plan" in world.screen.handed[-1]
    await _settled(world.factory, delivery_id, {"received"})
    assert world.screen.moved_aside(world.conversation) >= 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "own_turn",
    [{CLOSES_TASK: True}, {"eventType": EVENT_ROUTINE_RUN}, {PURPOSE: OPENING}],
    ids=["task write-up", "routine run", "task opening"],
)
async def test_a_delivery_that_is_its_own_work_goes_in_at_once_as_its_own(
    world, own_turn
):
    await world.busy()
    delivery_id = await _delivery(
        world.factory,
        world.conversation,
        world.instance_id,
        "write the task up",
        payload=own_turn,
    )

    assert await world.dispatch(delivery_id) == 1

    row = await _settled(world.factory, delivery_id, {"received"})
    assert row.sent_at is not None
    assert "write the task up" in world.screen.handed[-1]
    (own,) = await _inputs_of(world.factory, row.attempt_id)
    # Its own input, under its own work: not one more word said to the work
    # already running, though that is where the session read it.
    assert own.work_id == own.input_id == row.attempt_id
    assert own.execution_work_id != own.work_id
    assert world.screen.moved_aside(world.conversation) == 0


async def _inputs_of(factory, work_id) -> list[NativeInput]:
    async with factory() as session:
        return list(
            await session.scalars(
                select(NativeInput).where(NativeInput.work_id == work_id)
            )
        )


@pytest.mark.anyio
async def test_a_notice_the_running_turn_did_not_take_goes_in_as_its_own(
    world, monkeypatch
):
    await world.busy()

    async def turned_away(*args, **kwargs):
        return False

    monkeypatch.setattr(world.chat._compute, "steer", turned_away)
    delivery_id = await _delivery(
        world.factory,
        world.conversation,
        world.instance_id,
        "the checks on PR 12 failed",
    )

    assert await world.dispatch(delivery_id) == 1

    row = await _settled(world.factory, delivery_id, {"received"})
    assert row.sent_at is not None
    assert "the checks on PR 12 failed" in world.screen.handed[-1]
