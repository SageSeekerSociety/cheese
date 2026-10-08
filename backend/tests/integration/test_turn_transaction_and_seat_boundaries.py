"""Real commits and session readings at the intake/execution boundaries."""

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.block.models import Block
from app.domain.delivery.input_identity import WorkCompletion
from app.domain.delivery.models import NativeInput
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import registered
from tests.support.hang import HANG_S
from tests.support.threads import thread_in


class HeldSeats(StubChannel):
    """Only the machine I/O is scripted; all intake and journal consumers are real."""

    def __init__(self):
        super().__init__()
        self.writes: list[tuple[uuid.UUID, str]] = []
        self.written = asyncio.Event()

    def arrive(self, topic_id, message, *, agent=None):
        assert agent is not None
        self.writes.append((topic_id, agent))
        self.starts(topic_id, agent=agent)
        content = message["message"]["content"]
        prompt = content if isinstance(content, str) else content[0]["text"]
        self.acknowledges(topic_id, prompt, agent=agent)
        self.written.set()


async def a_conversation(factory, *, two_seats=False):
    async with factory() as session:
        await registered(session, "u")
        project = await ProjectService(session).create(name="P", owner_handle="u")
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        members = TopicMemberService(session)
        first = (await members.agent_handles(room.id))[0]
        second = None
        if two_seats:
            other = await AgentInstanceService(session).create(
                project_id=project.id,
                handle="second",
                type_name=None,
                display_name="Second",
            )
            second = agent_instance_handle(other.id)
            await members.ensure_agent_seat(room.id, second)
        conversation = await thread_in(session, room)
        await session.commit()
    return conversation, first, second


async def until_frame(queue, kind, *, turn_id=None):
    async with asyncio.timeout(HANG_S):
        while True:
            frame = await queue.get()
            if frame["type"] == kind and (
                turn_id is None or frame.get("turn_id") == str(turn_id)
            ):
                return frame


@pytest.mark.anyio
@pytest.mark.parametrize("commit", [True, False], ids=["commit", "abort"])
async def test_human_commit_precedes_echo_and_real_execution(
    business_db_factory, tmp_path, monkeypatch, commit
):
    factory = business_db_factory
    conversation, first, _ = await a_conversation(factory)
    reached = asyncio.Event()
    release = asyncio.Event()
    text = f"<@{first}> boundary-{uuid.uuid4()}"
    gated = False

    class GatedSession(AsyncSession):
        async def commit(self):
            nonlocal gated
            # Read our flushed, uncommitted row, then park the REAL commit.
            # Other preparation transactions do not match this unique message.
            row = await self.scalar(select(Block).where(Block.content == text))
            if row is not None and not gated:
                gated = True
                reached.set()
                await release.wait()
                if not commit:
                    raise RuntimeError("intentional human commit abort")
            await super().commit()

    gated_factory = async_sessionmaker(
        factory.kw["bind"], class_=GatedSession, expire_on_commit=False
    )
    screen = HeldSeats()
    broker = get_broker()
    runner = AgentWorkRunner(broker)
    chat = ChatService(
        work_runner=runner,
        session_factory=gated_factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    preparation_calls = []
    published = []
    real_prepare = chat.turn_preparation.prepare
    real_publish = broker.publish

    async def observe_publish(channel, frame):
        await real_publish(channel, frame)
        if channel == str(conversation):
            published.append(frame["type"])

    async def observe_prepare(**facts):
        preparation_calls.append(facts)
        assert "user_block" in published
        async with factory() as observer:
            assert await observer.scalar(select(Block).where(Block.content == text))
        return await real_prepare(**facts)

    def actual_write():
        assert "user_block" in published
        assert len(preparation_calls) == 1

    monkeypatch.setattr(broker, "publish", observe_publish)
    monkeypatch.setattr(chat.turn_preparation, "prepare", observe_prepare)
    screen.on_start = actual_write
    async with broker.subscribe(str(conversation)) as browser:
        receive = asyncio.create_task(
            runner.receive_message(chat, conversation, author="u", content=text)
        )
        try:
            await asyncio.wait_for(reached.wait(), HANG_S)
            assert not receive.done()
            assert browser.empty()
            assert screen.writes == []
            assert preparation_calls == []
            assert runner.active_work_count == 0
            # The writer holds its connection; this read has a separate one.
            async with factory() as observer:
                assert (
                    await observer.scalar(select(Block).where(Block.content == text))
                    is None
                )
                assert (
                    list(
                        await observer.scalars(
                            select(AgentTurn).where(
                                AgentTurn.conversation_id == conversation
                            )
                        )
                    )
                    == []
                )
            release.set()
            if not commit:
                with pytest.raises(
                    RuntimeError, match="intentional human commit abort"
                ):
                    await receive
                assert browser.empty()
                assert screen.writes == []
                assert preparation_calls == []
                assert runner.active_work_count == 0
                async with factory() as observer:
                    assert (
                        await observer.scalar(
                            select(Block).where(Block.content == text)
                        )
                        is None
                    )
                return
            block_id = await receive
            # The first visible frame is the committed message, not thinking.
            echo = await asyncio.wait_for(browser.get(), HANG_S)
            assert isinstance(echo, dict)
            assert echo["type"] == "user_block"
            assert echo["block"]["id"] == str(block_id)
            async with factory() as observer:
                assert (await observer.get(Block, block_id)).content == text
            await asyncio.wait_for(screen.written.wait(), HANG_S)
            assert screen.writes == [(conversation, screen.writes[0][1])]
            async with factory() as observer:
                interval = await observer.get(AgentTurn, block_id)
                assert interval is not None and interval.stopped_at is None
            seat = screen.writes[0][1]
            screen.says(conversation, "committed response", agent=seat)
            screen.stops(conversation, "committed response", agent=seat)
            await until_frame(browser, "turn_finished", turn_id=block_id)
            await runner.drain(timeout_s=60)
            async with factory() as observer:
                assert (await observer.get(AgentTurn, block_id)).stopped_at is not None
        finally:
            release.set()
            if not receive.done():
                receive.cancel()
            await asyncio.gather(receive, return_exceptions=True)
            await chat.stop_listening(timeout_s=2)


@pytest.mark.anyio
async def test_one_seat_completion_and_duplicate_leave_the_other_live(
    business_db_factory, tmp_path
):
    factory = business_db_factory
    conversation, first, second = await a_conversation(factory, two_seats=True)
    screen = HeldSeats()
    broker = get_broker()
    runner = AgentWorkRunner(broker)
    chat = ChatService(
        work_runner=runner,
        session_factory=factory,
        compute=stub_compute(screen),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    async with broker.subscribe(str(conversation)) as browser:
        try:
            first_turn = await runner.receive_message(
                chat, conversation, author="u", content=f"<@{first}> first work"
            )
            await asyncio.wait_for(screen.written.wait(), HANG_S)
            screen.written.clear()
            second_turn = await runner.receive_message(
                chat, conversation, author="u", content=f"<@{second}> second work"
            )
            await asyncio.wait_for(screen.written.wait(), HANG_S)
            async with asyncio.timeout(HANG_S):
                while not (
                    chat.session_took_over(conversation, first_turn)
                    and chat.session_took_over(conversation, second_turn)
                ):
                    await asyncio.sleep(0.01)
            async with factory() as observer:
                inputs = list(
                    await observer.scalars(
                        select(NativeInput).where(
                            NativeInput.conversation_id == conversation,
                            NativeInput.work_id == first_turn,
                        )
                    )
                )
                assert inputs
                own = inputs[0]
                duplicate = WorkCompletion(
                    own.project_id,
                    conversation,
                    own.recipient_handle,
                    own.harness,
                    own.native_session_id,
                    first_turn,
                    tuple(row.input_id for row in inputs),
                )
                other_before = await observer.get(AgentTurn, second_turn)
                assert other_before.stopped_at is None
            first_seat, second_seat = screen.writes[0][1], screen.writes[1][1]
            screen.says(conversation, "first output", agent=first_seat)
            screen.stops(conversation, "first output", agent=first_seat)
            await until_frame(browser, "turn_finished", turn_id=first_turn)
            await chat.confirm_work_completion(duplicate)
            await chat.confirm_work_completion(duplicate)
            assert chat.session_took_over(conversation, second_turn)
            assert str(second_turn) in broker.active_turn_ids(str(conversation))
            assert str(first_turn) not in broker.active_turn_ids(str(conversation))
            async with factory() as observer:
                assert (
                    await observer.get(AgentTurn, first_turn)
                ).stopped_at is not None
                assert (await observer.get(AgentTurn, second_turn)).stopped_at is None
            async with broker.subscribe(str(conversation), replay=True) as replay:
                replayed = [replay.get_nowait() for _ in range(replay.qsize())]
            assert all(isinstance(frame, dict) for frame in replayed)
            replayed = [frame for frame in replayed if isinstance(frame, dict)]
            assert any(
                frame["type"] == "turn_started" and frame["turn_id"] == str(second_turn)
                for frame in replayed
            )
            assert not any(
                frame["type"] == "turn_started" and frame["turn_id"] == str(first_turn)
                for frame in replayed
            )
            screen.says(conversation, "second still works", agent=second_seat)
            output = await until_frame(browser, "event_block")
            while output["block"]["content"] != "second still works":
                output = await until_frame(browser, "event_block")
            assert output["block"]["turn_id"] == str(second_turn)
            screen.stops(conversation, "second finished", agent=second_seat)
            await until_frame(browser, "turn_finished", turn_id=second_turn)
            await runner.drain(timeout_s=60)
            async with factory() as observer:
                assert (
                    await observer.get(AgentTurn, second_turn)
                ).stopped_at is not None
            assert broker.active_turn_ids(str(conversation)) == []
            assert not chat.has_running_turn(conversation)
        finally:
            await chat.stop_listening(timeout_s=2)
