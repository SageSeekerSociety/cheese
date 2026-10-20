"""An instruction that waited for its agent's seat goes in when the seat frees.

A delivery whose agent's session holds an input it was written and has not
read yet is not sent: what became of that input is unknown, so the attempt
goes back to the ledger with a backoff. The session reading it and its work
ending, as its runner reports them, is what the delivery was waiting for, and
what wakes it, as it wakes the messages a person sent meanwhile. On dev
(2026-10-15) an orchestrator's instructions waited out their backoff past the
gaps between its turns, and 48 of them in eight days were given up after half
an hour without ever meeting a free seat.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api.deps import get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.delivery.agent import RETRY_SECONDS, dispatch_pending
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.notification.models import NotificationType
from app.domain.room_task.closing import CLOSES_TASK
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import project_service, registered
from tests.support.hang import HANG_S
from tests.support.threads import thread_in


class Seat(StubChannel):
    """Records each input the agent's session is handed. An input whose text
    contains one of ``unread`` it is written and does not read (``took``);
    every other one it reads (``acknowledges``)."""

    def __init__(self, *unread: str):
        super().__init__()
        self.unread = unread
        self.handed: list[str] = []
        self.taken: dict[str, str | None] = {}
        self.handed_one = asyncio.Event()

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        self.handed.append(prompt)
        self.starts(topic_id, agent=agent)
        if any(text in prompt for text in self.unread):
            self.taken[prompt] = self.took(topic_id, prompt, agent=agent)
        else:
            self.acknowledges(topic_id, prompt, agent=agent)
        self.handed_one.set()

    def reads(self, topic_id, text, *, agent):
        prompt = next(p for p in self.handed if text in p)
        self.acknowledges(topic_id, prompt, agent=agent)

    def discards(self, topic_id, text, *, agent):
        """The build settles that input unread: it will never be read."""
        prompt = next(p for p in self.handed if text in p)
        self.record(
            topic_id,
            agent=agent,
            type="command_lifecycle",
            command_uuid=self.taken[prompt],
            state="discarded",
        )


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


async def _instruction_for(factory, conversation, instance_id, content, **payload):
    async with factory() as session:
        stamp = datetime.now(UTC)
        delivery = Delivery(
            event_id=uuid.uuid4(),
            recipient_handle=agent_instance_handle(instance_id),
            agent_instance_id=instance_id,
            conversation_id=conversation,
            dedup_key=f"{uuid.uuid4()}:freed-seat",
            type=NotificationType.ROOM_NOTICE.value,
            payload={"content": content, **payload},
            event_at=stamp,
            recorded_at=stamp,
        )
        session.add(delivery)
        await session.commit()
        return delivery.id


async def _row(factory, delivery_id):
    async with factory() as session:
        return await session.scalar(
            select(Delivery)
            .where(Delivery.id == delivery_id)
            .execution_options(populate_existing=True)
        )


@pytest.mark.anyio
async def test_the_instruction_goes_in_when_the_work_it_waited_for_ends(
    business_db_factory, tmp_path
):
    factory = business_db_factory
    conversation, seat, instance_id = await _room_with_agent(factory)
    actor = agent_instance_handle(instance_id)
    screen = Seat("a long piece of work")
    # The process's own runner: the one a finished work's wake-ups run on.
    runner = get_work_runner()
    chat = ChatService(
        work_runner=runner,
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    try:
        # The agent is busy: a person gave it work, and it has not finished.
        await runner.receive_message(
            chat, conversation, author="u", content=f"<@{seat}> a long piece of work"
        )
        await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
        screen.handed_one.clear()

        # An instruction for the same agent arrives meanwhile and is tried
        # once, while the seat holds the input it has not read.
        delivery_id = await _instruction_for(
            factory,
            conversation,
            instance_id,
            "check the open pull requests",
            **{CLOSES_TASK: True},
        )
        assert await dispatch_pending(factory, chat=chat, runner=runner) == 1
        async with asyncio.timeout(HANG_S):
            while (row := await _row(factory, delivery_id)).state != "pending":
                await asyncio.sleep(0.05)
        # Not sent: the seat holds the input its session has not read.
        assert row.sent_at is None and row.attempts == 1
        assert len(screen.handed) == 1

        # The session reads it and its work ends. Nothing else happens in the
        # room, and nobody scans the ledger: the backoff is far from over.
        screen.reads(conversation, "a long piece of work", agent=actor)
        screen.says(conversation, "done", agent=actor)
        screen.stops(conversation, "done", agent=actor)

        # Well inside the first backoff, the instruction is with the agent.
        await asyncio.wait_for(screen.handed_one.wait(), RETRY_SECONDS / 3)
        assert "check the open pull requests" in screen.handed[-1]
        async with asyncio.timeout(HANG_S):
            while (row := await _row(factory, delivery_id)).sent_at is None:
                await asyncio.sleep(0.05)
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_a_message_sent_meanwhile_goes_in_once_the_held_input_is_dropped(
    business_db_factory, tmp_path
):
    """The agent's work ended with an input it was written mid-work and never
    read: what became of it is unknown, so a person's next message waits. The
    build then settles that input unread: the seat is free, and the message
    goes in without anyone writing again."""
    factory = business_db_factory
    conversation, seat, instance_id = await _room_with_agent(factory)
    actor = agent_instance_handle(instance_id)
    screen = Seat("and the tests")
    runner = get_work_runner()
    chat = ChatService(
        work_runner=runner,
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    try:
        await runner.receive_message(
            chat, conversation, author="u", content=f"<@{seat}> fix the login page"
        )
        await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
        screen.handed_one.clear()
        # Said while the work runs: written to the session, not read.
        await runner.receive_message(
            chat, conversation, author="u", content=f"<@{seat}> and the tests"
        )
        await asyncio.wait_for(screen.handed_one.wait(), HANG_S)
        screen.handed_one.clear()
        screen.says(conversation, "done", agent=actor)
        screen.stops(conversation, "done", agent=actor)
        await asyncio.sleep(0.5)

        await runner.receive_message(
            chat, conversation, author="u", content=f"<@{seat}> and the docs"
        )
        await asyncio.sleep(0.5)
        assert not any("and the docs" in prompt for prompt in screen.handed)

        screen.discards(conversation, "and the tests", agent=actor)

        await asyncio.wait_for(screen.handed_one.wait(), RETRY_SECONDS / 3)
        assert "and the docs" in screen.handed[-1]
    finally:
        await runner.drain(timeout_s=10)
        await chat.stop_listening(timeout_s=2)
