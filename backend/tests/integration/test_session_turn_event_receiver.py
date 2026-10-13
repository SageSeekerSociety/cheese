"""A session's interval, output and completion belong to its supplied runner."""

import asyncio
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.domain.agent.chat import ChatService
from app.domain.agent.gateway_usage import OWN_ROUTE
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.models import AgentTurn
from app.domain.agent.realtime.broker import InProcessBroker
from app.domain.agent.runtime import AgentWorkRunner
from app.domain.agent.service import AgentMessage, AgentResult, AgentToolUse
from app.domain.agent.turn.steps import hooks as hook_steps
from app.domain.block.models import Block, consumed_turn
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.input_identity import InputEffects, InputReceipt
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks, record_receipt, register_input
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.usage.models import ResourceUsage
from tests.conftest import stub_compute
from tests.integration import test_own_claude_code_turns as own_turns
from tests.integration.conftest import registered
from tests.integration.test_native_batch_ownership import _blocks, _identity
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


@pytest.mark.anyio
@pytest.mark.parametrize("cancelled", [False, True], ids=["sql-abort", "commit-cancel"])
async def test_actual_hook_close_failure_preserves_context_and_runner_until_replay(
    business_db_factory, tmp_path, monkeypatch, cancelled
):
    """Fail the real settlement commit through intake and Stop execution.

    Cancellation is injected by the commit event, not the driver or a task
    cancellation after commit. One failed Stop and one replay do not establish
    exactly-once accounting across multiple successful Stops.
    """
    factory = business_db_factory
    project, topic = await _room(factory, "Replayable native Stop")
    seat = await own_turns._seat(factory, project)
    broker = InProcessBroker()
    monkeypatch.setattr(hook_steps, "get_broker", lambda: broker)
    runner = AgentWorkRunner(broker)
    chat = _chat(factory, tmp_path, runner)
    chat.live.session_route[topic] = OWN_ROUTE
    work = uuid.uuid4()
    frames = []
    publish = broker.publish

    async def observe_publish(channel, frame):
        await publish(channel, frame)
        frames.append((channel, frame))

    monkeypatch.setattr(broker, "publish", observe_publish)
    await _consume(
        chat,
        project,
        topic,
        work,
        AgentToolUse(
            name="Bash",
            input={"command": "true"},
            agent_handle=seat,
            session_id="native-session",
        ),
        "native-stop-opener",
    )
    key = (topic, work)
    state = chat.live.hook_work[key]
    assert state.self_started and state.route == OWN_ROUTE
    assert state.acting_agent == seat
    context_identity = (
        state.project_id,
        state.topic_id,
        state.work_id,
        state.acting_agent,
        state.agent_instance_handle,
        state.continuation_id,
    )
    assert str(work) in runner._last_frame_at
    assert runner._live_topics[str(work)] == topic
    assert runner.recent_work()[0]["status"] == "running"
    assert topic in runner.running_topic_ids()

    try:
        identity = replace(
            _identity(project, topic, receiver=state.acting_agent),
            work_id=work,
            harness=CLAUDE_CODE,
            native_session_id="native-session",
        )
        ids = await _blocks(factory, project, topic)
        input_query = select(NativeInput).where(
            NativeInput.project_id == project,
            NativeInput.conversation_id == topic,
            NativeInput.recipient_handle == seat,
            NativeInput.input_id == identity.input_id,
            NativeInput.work_id == work,
        )
        blocks_query = select(Block).where(Block.id.in_(ids))
        usage_query = select(ResourceUsage).where(ResourceUsage.turn_id == work)
        intervals_query = select(AgentTurn).where(AgentTurn.conversation_id == topic)
        async with factory() as session:
            await register_input(session, identity, InputEffects(held_block_ids=ids))
            await record_receipt(
                session,
                InputReceipt(identity, "native_echo", execution_work_id=work),
            )
            await session.commit()
            assert list(await session.scalars(usage_query)) == []
        result = own_turns._result(seat, own_turns._finished(seat, result=""))
        assert not result.is_error and result.input_work_completed
        assert result.session_id == identity.native_session_id
        assert result.harness == identity.harness == CLAUDE_CODE
        assert result.agent_handle == identity.recipient_handle == state.acting_agent
        assert identity.work_id == state.work_id == work
        frames.clear()
        closing = asyncio.current_task()
        reached = []

        def fail_settlement_commit(session):
            if asyncio.current_task() is not closing:
                return
            session.flush()
            row = session.scalars(input_query).one()
            usages = list(session.scalars(usage_query))
            # Intake saves the pointer and Stop closes the interval first, in
            # separate commits. Fail only after the real joint settlement writes.
            if row.completed_at is None or not usages:
                return
            assert row.echoed_at is not None
            assert set(row.released_block_ids) == {str(block_id) for block_id in ids}
            assert {
                consumed_turn(block) for block in session.scalars(blocks_query)
            } == {str(work)}
            (usage,) = usages
            assert usage.output_tokens == own_turns.USAGE["output_tokens"]
            assert usage.credits == 0
            reached.append(True)
            if cancelled:
                raise asyncio.CancelledError("cancel inside native Stop commit event")
            session.execute(text("SELECT 1 / 0"))

        event.listen(Session, "before_commit", fail_settlement_commit)
        try:
            with pytest.raises(asyncio.CancelledError if cancelled else DBAPIError):
                await _consume(chat, project, topic, work, result)
        finally:
            event.remove(Session, "before_commit", fail_settlement_commit)
        assert reached == [True], "the failure must reach the real settlement commit"
        assert chat.live.hook_work[key] is state
        assert state.self_started
        assert (
            state.project_id,
            state.topic_id,
            state.work_id,
            state.acting_agent,
            state.agent_instance_handle,
            state.continuation_id,
        ) == context_identity
        assert str(work) in runner._last_frame_at
        assert runner._live_topics[str(work)] == topic
        assert runner.recent_work()[0]["status"] == "running"
        assert topic in runner.running_topic_ids()
        assert not any(frame["type"] == "done" for _, frame in frames)
        async with factory() as independent:
            row = (await independent.scalars(input_query)).one()
            assert row.echoed_at is not None and row.completed_at is None
            assert row.released_block_ids == []
            assert all(
                consumed_turn(block) is None
                for block in await independent.scalars(blocks_query)
            )
            assert await held_blocks(
                independent, project_id=project, topic_id=topic, recipient_handle=seat
            ) == set(ids)
            assert list(await independent.scalars(usage_query)) == []
            (interval,) = list(await independent.scalars(intervals_query))
            assert interval.id == work and interval.stopped_at is not None
            assert interval.session_id == identity.native_session_id

        # Replay the same result through actual intake and Stop execution, not a
        # direct completion.close helper. The existing interval must not reopen.
        await _consume(chat, project, topic, work, result)
        assert key not in chat.live.hook_work
        assert str(work) not in runner._last_frame_at
        assert str(work) not in runner._live_topics
        assert runner.recent_work()[0]["status"] == "done"
        assert topic not in runner.running_topic_ids()
        assert [channel for channel, frame in frames if frame["type"] == "done"] == [
            str(topic)
        ]
        async with factory() as independent:
            row = (await independent.scalars(input_query)).one()
            assert row.echoed_at is not None and row.completed_at is not None
            assert set(row.released_block_ids) == {str(block_id) for block_id in ids}
            assert {
                consumed_turn(block)
                for block in await independent.scalars(blocks_query)
            } == {str(work)}
            assert (
                await held_blocks(
                    independent,
                    project_id=project,
                    topic_id=topic,
                    recipient_handle=seat,
                )
                == set()
            )
            (usage,) = list(await independent.scalars(usage_query))
            assert usage.credits == 0
            assert usage.output_tokens == own_turns.USAGE["output_tokens"]
            (interval,) = list(await independent.scalars(intervals_query))
            assert interval.id == work and interval.stopped_at is not None
            assert interval.session_id == identity.native_session_id
    finally:
        # Only this test's native context created this baseline future.
        if state.known_commits is not None:
            if not state.known_commits.done():
                state.known_commits.cancel()
            await asyncio.gather(state.known_commits, return_exceptions=True)
