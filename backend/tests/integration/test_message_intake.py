"""Receiving a message is durable before its echo; subscribers do not own work."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from tests.conftest import StubChannel, finish_turn, stub_compute
from tests.integration.conftest import registered
from tests.support.hang import HANG_S
from tests.support.threads import thread_in


async def _room(factory, *, in_thread=False):
    async with factory() as session:
        await registered(session, "alice")
        project = await ProjectService(session).create(
            name="Intake", owner_handle="alice"
        )
        topic = await TopicService(session).create(
            project_id=project.id, title="Work", created_by="alice"
        )
        conversation = await thread_in(session, topic) if in_thread else topic.id
        await session.commit()
    return conversation


def _chat(factory, tmp_path, channel=None):
    return ChatService(
        session_factory=factory,
        compute=stub_compute(channel or StubChannel()),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "workspace"),
    )


@pytest.mark.anyio
async def test_human_echo_is_already_readable_on_an_independent_connection(
    business_db_factory, tmp_path, monkeypatch
):
    factory = business_db_factory
    topic = await _room(factory)
    chat = _chat(factory, tmp_path)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    release = asyncio.Event()
    publish = broker.publish

    async def paused_echo(channel, frame):
        await publish(channel, frame)
        if frame.get("type") == "user_block":
            # Keep intake parked at publication: waiting for receive to finish
            # would also pass if a future implementation committed too late.
            await release.wait()

    monkeypatch.setattr(broker, "publish", paused_echo)
    received = None
    try:
        async with broker.subscribe(str(topic)) as browser:
            received = asyncio.create_task(
                runner.receive_message(
                    chat, topic, author="alice", content="First fact"
                )
            )
            frame = await asyncio.wait_for(browser.get(), HANG_S)
            assert frame["type"] == "user_block"
            assert not received.done()
            async with factory() as independent:
                saved = await BlockRepository(independent).get(
                    uuid.UUID(frame["block"]["id"])
                )
                assert saved is not None
                assert saved.content == frame["block"]["content"] == "First fact"
                assert saved.author == "alice"
            release.set()
            assert await asyncio.wait_for(received, HANG_S) == saved.id
    finally:
        release.set()
        if received is not None:
            await asyncio.wait_for(received, HANG_S)
        await runner.drain()


@pytest.mark.anyio
async def test_a_failed_message_commit_echoes_nothing_and_schedules_nothing(
    business_db_factory, tmp_path, monkeypatch
):
    factory = business_db_factory
    topic = await _room(factory)

    @asynccontextmanager
    async def failing_sessions():
        async with factory() as session:

            async def refuse_commit():
                raise RuntimeError("message transaction aborted")

            monkeypatch.setattr(session, "commit", refuse_commit)
            yield session

    chat = _chat(failing_sessions, tmp_path)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    scheduled = Mock()
    monkeypatch.setattr(runner, "_receive_message", scheduled)
    async with broker.subscribe(str(topic)) as browser:
        with pytest.raises(RuntimeError, match="message transaction aborted"):
            await runner.receive_message(
                chat, topic, author="alice", content="@芝士 This must not land"
            )
        assert browser.empty()
    scheduled.assert_not_called()
    async with factory() as independent:
        assert await BlockRepository(independent).list_for_topic(topic) == []


class _HeldAnswer(StubChannel):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.answer = None

    def arrive(self, topic_id, message, *, agent=None):
        prompt = message["message"]["content"]
        self.acknowledges(topic_id, prompt, agent=agent)
        self.started.set()
        self.answer = asyncio.create_task(self._answer(topic_id, agent))

    async def _answer(self, topic_id, agent):
        await self.release.wait()
        self.says(topic_id, "Completed after the browser left", agent=agent)
        self.stops(topic_id, "Completed after the browser left", agent=agent)


@pytest.mark.anyio
async def test_disconnect_during_work_does_not_lose_output_or_leave_the_turn_open(
    business_db_factory, tmp_path
):
    factory = business_db_factory
    topic = await _room(factory, in_thread=True)
    screen = _HeldAnswer()
    chat = _chat(factory, tmp_path, screen)
    broker = InProcessBroker()
    runner = AgentWorkRunner(broker)
    try:
        async with broker.subscribe(str(topic)) as browser:
            await runner.receive_message(
                chat, topic, author="alice", content="@芝士 Finish the work"
            )
            assert (await asyncio.wait_for(browser.get(), HANG_S))[
                "type"
            ] == "user_block"
            await asyncio.wait_for(screen.started.wait(), HANG_S)
        # There are no subscribers left when the executor produces its answer.
        screen.release.set()
        assert screen.answer is not None
        await asyncio.wait_for(screen.answer, HANG_S)
        await runner.drain(timeout_s=60)
        await finish_turn(chat, topic)
        async with factory() as independent:
            history = await BlockRepository(independent).list_for_topic(topic)
            turns = list(
                await independent.scalars(
                    select(AgentTurn).where(AgentTurn.conversation_id == topic)
                )
            )
        assert any(
            block.content == "Completed after the browser left" for block in history
        )
        assert turns
        assert all(turn.delivered_at is not None for turn in turns)
        assert all(turn.stopped_at is not None for turn in turns)
    finally:
        screen.release.set()
        if screen.answer is not None:
            await asyncio.wait_for(screen.answer, HANG_S)
        await runner.drain(timeout_s=60)
        await finish_turn(chat, topic)
