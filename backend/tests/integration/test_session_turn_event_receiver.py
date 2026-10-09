"""A session's interval, output and completion belong to its supplied runner."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.service import AgentMessage, AgentResult, AgentToolUse
from app.domain.agent.turn.steps import hooks as hook_steps
from app.domain.block.repositories import BlockRepository
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.usage.models import ResourceUsage
from tests.conftest import stub_compute
from tests.integration.conftest import registered
from tests.support.hang import HANG_S
from tests.support.threads import thread_in


async def _room(factory, title):
    async with factory() as session:
        await registered(session, "alice")
        project = await ProjectService(session).create(name=title, owner_handle="alice")
        room = await TopicService(session).create(
            project_id=project.id, title=title, created_by="alice"
        )
        conversation = await thread_in(session, room)
        await session.commit()
    return project.id, conversation


def _chat(factory, tmp_path, runner):
    return ChatService(
        session_factory=factory,
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path),
        compute=stub_compute(),
        work_runner=runner,
    )


async def _consume(chat, project, topic, work, event, eid=None):
    await chat.hook_events.accept(project, topic, work, event, eid, False, True)


@pytest.mark.anyio
@pytest.mark.parametrize("tool", [False, True])
async def test_the_supplied_runner_observes_durable_output_and_completed_work(
    business_db_factory, tmp_path, monkeypatch, tool
):
    factory = business_db_factory
    project, topic = await _room(factory, "Session events")
    broker = InProcessBroker()
    monkeypatch.setattr(hook_steps, "get_broker", lambda: broker)
    runner = AgentWorkRunner(broker)
    chat = _chat(factory, tmp_path, runner)
    work = uuid.uuid4()
    event = (
        AgentToolUse(name="Bash", input={"command": "true"})
        if tool
        else AgentMessage(text="A session working on its own")
    )
    await _consume(chat, project, topic, work, event, "own-output")
    summary = runner.recent_work()[0]
    assert summary["turn_id"] == str(work)
    assert summary["first_output_s"] is not None
    assert summary["tools"] == int(tool)
    assert topic in runner.running_topic_ids()
    async with factory() as independent:
        row = await independent.get(AgentTurn, work)
        assert row is not None and row.delivered_at is not None
        assert row.stopped_at is None and not row.resendable
        (output,) = [
            block
            for block in await BlockRepository(independent).list_for_topic(topic)
            if block.turn_id == work
        ]
        assert output.conversation_id == topic
        assert output.meta is not None
        assert output.meta["eid"] == "own-output"
        if tool:
            assert output.meta["tool"] == "Bash"
        else:
            assert output.content == "A session working on its own"

    await _consume(chat, project, topic, work, AgentResult(text="", session_id=None))
    # No finish_turn helper, coroutine finally or sweep completes this afterward.
    assert runner.recent_work()[0]["status"] == "done"
    assert topic not in runner.running_topic_ids()
    async with factory() as independent:
        assert (await independent.get(AgentTurn, work)).stopped_at is not None
        assert (
            len(
                list(
                    await independent.scalars(
                        select(ResourceUsage).where(ResourceUsage.turn_id == work)
                    )
                )
            )
            == 1
        )

    await _consume(chat, project, topic, work, AgentResult(text="", session_id=None))
    async with factory() as independent:
        assert (
            len(
                list(
                    await independent.scalars(
                        select(AgentTurn).where(AgentTurn.conversation_id == topic)
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    await independent.scalars(
                        select(ResourceUsage).where(ResourceUsage.turn_id == work)
                    )
                )
            )
            == 1
        )


@pytest.mark.anyio
async def test_open_and_publish_are_awaited_before_output_and_close_notifications(
    business_db_factory, tmp_path, monkeypatch
):
    factory = business_db_factory
    project, topic = await _room(factory, "Awaited session events")
    other_project, other_topic = await _room(factory, "Another receiver")
    broker = InProcessBroker()
    monkeypatch.setattr(hook_steps, "get_broker", lambda: broker)
    opening, allow_open = asyncio.Event(), asyncio.Event()

    class WaitingRunner(AgentWorkRunner):
        async def open_turn_the_session_started(self, *args, **kwargs):
            opening.set()
            await allow_open.wait()
            await super().open_turn_the_session_started(*args, **kwargs)

    runner = WaitingRunner(broker)
    other_runner = AgentWorkRunner(InProcessBroker())
    chat = _chat(factory, tmp_path, runner)
    other_chat = _chat(factory, tmp_path, other_runner)
    work, other_work = uuid.uuid4(), uuid.uuid4()
    await other_chat.native_turns.begin(other_project, other_topic, other_work)
    publishing, allow_publish = asyncio.Event(), asyncio.Event()
    publish = broker.publish

    async def waiting_publish(channel, frame):
        if channel == str(topic) and frame["type"] == "event_block":
            publishing.set()
            await allow_publish.wait()
        await publish(channel, frame)

    monkeypatch.setattr(broker, "publish", waiting_publish)
    consuming = asyncio.create_task(
        _consume(
            chat,
            project,
            topic,
            work,
            AgentToolUse(name="Bash", input={"command": "true"}),
            "awaited-tool",
        )
    )
    try:
        await asyncio.wait_for(opening.wait(), HANG_S)
        assert not consuming.done()
        assert runner.recent_work() == []
        async with factory() as independent:
            assert await independent.get(AgentTurn, work) is None
            assert await BlockRepository(independent).list_for_topic(topic) == []
        allow_open.set()
        await asyncio.wait_for(publishing.wait(), HANG_S)
        assert not consuming.done()
        assert runner.recent_work()[0]["first_output_s"] is None
        assert runner.recent_work()[0]["tools"] == 0
        async with factory() as independent:
            assert (await independent.get(AgentTurn, work)).delivered_at is not None
            (output,) = [
                block
                for block in await BlockRepository(independent).list_for_topic(topic)
                if block.turn_id == work
            ]
            assert output.conversation_id == topic
            assert output.meta is not None
            assert output.meta["eid"] == "awaited-tool"
            assert output.meta["tool"] == "Bash"
        allow_publish.set()
        await asyncio.wait_for(consuming, HANG_S)
        assert runner.recent_work()[0]["tools"] == 1
        assert other_runner.recent_work()[0]["first_output_s"] is None
        assert other_runner.recent_work()[0]["tools"] == 0
        assert other_runner.recent_work()[0]["status"] == "running"

        closing, allow_close = asyncio.Event(), asyncio.Event()
        from app.domain.agent.turn.intake.completion import TurnCompletion

        close = TurnCompletion.close

        async def waiting_close(completion, state, result):
            closing.set()
            await allow_close.wait()
            return await close(completion, state, result)

        monkeypatch.setattr(TurnCompletion, "close", waiting_close)
        consuming = asyncio.create_task(
            _consume(chat, project, topic, work, AgentResult(text="", session_id=None))
        )
        await asyncio.wait_for(closing.wait(), HANG_S)
        assert not consuming.done()
        assert runner.recent_work()[0]["status"] == "running"
        async with factory() as independent:
            assert (await independent.get(AgentTurn, work)).stopped_at is not None
        allow_close.set()
        await asyncio.wait_for(consuming, HANG_S)
        assert runner.recent_work()[0]["status"] == "done"
        assert runner.running_topic_ids() == set()
        assert other_runner.running_topic_ids() == {other_topic}
        async with factory() as independent:
            assert (await independent.get(AgentTurn, other_work)).stopped_at is None
    finally:
        allow_open.set()
        allow_publish.set()
        if "allow_close" in locals():
            allow_close.set()
        await asyncio.wait_for(consuming, HANG_S)
        await _consume(
            other_chat,
            other_project,
            other_topic,
            other_work,
            AgentResult(text="", session_id=None),
        )
