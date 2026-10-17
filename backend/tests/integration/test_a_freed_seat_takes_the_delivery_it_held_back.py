"""An instruction that waited for its agent's seat goes in when the seat frees.

A delivery that takes a turn of its own, whose agent is still working on an
earlier input, is not sent: the seat holds that input until the work ends, so
the attempt goes back to the ledger with a backoff. The work ending is what it
was waiting for — the messages a person sent meanwhile are woken by that same
ending. On dev (2026-10-15) an orchestrator's instructions waited out their
backoff past the gaps between its turns, and 48 of them in eight days were
given up after half an hour without ever meeting a free seat.
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
    screen = Seat()
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
        # once. It is one that takes a turn of its own — a finished task's
        # write-up — so it waits rather than joining the turn running there
        # (`test_a_delivery_joins_the_running_turn`).
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
        # Not sent: the seat was still holding the earlier work.
        assert row.sent_at is None and row.attempts == 1
        assert len(screen.handed) == 1

        # The work ends. Nothing else happens in the room, and nobody scans
        # the ledger: the backoff is far from over.
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
