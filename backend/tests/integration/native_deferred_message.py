"""New native continuation checks, separate from closed admission checkpoints."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.claude_code.runtime import ClaudeCodeRuntime
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block, consumed_turn, prompt_attempts
from app.domain.delivery.models import NativeInput
from app.main import app
from tests.conftest import settle_turn


async def finish_deferred_message(
    *,
    client,
    chat,
    runtime,
    channel,
    native_runner,
    machine,
    operations,
    ordinary_id,
    held_work,
    gate,
    mode,
    monkeypatch,
    topic,
    default_seat,
    recipient_handle,
):
    import app.domain.agent.chat as chat_module

    complete = chat_module.complete_work_inputs
    refused = asyncio.Event()
    allow = asyncio.Event()

    async def fail_before_commit(session, **identity):
        result = await complete(session, **identity)
        if identity["work_id"] == held_work and not allow.is_set():
            refused.set()
            raise RuntimeError("isolated native completion commit failure")
        return result

    monkeypatch.setattr(chat_module, "complete_work_inputs", fail_before_commit)
    native = native_runner.session_id
    process = native_runner.process
    sends = operations.count("send")
    gate.touch()
    async with asyncio.timeout(30):
        await refused.wait()
        while native_runner.working:
            await asyncio.sleep(0.01)
    async with client.test_request_factory() as session:
        waiting = await session.scalar(
            select(NativeInput).where(
                NativeInput.topic_id == topic,
                NativeInput.execution_work_id == held_work,
            )
        )
        assert waiting.completed_at is None
        ordinary = await session.get(Block, ordinary_id)
        assert ordinary.meta["deferred_native_input"] is True
        assert consumed_turn(ordinary) is None and prompt_attempts(ordinary) == 0
    # Idle and an explicit scan cannot substitute for the failed commit.
    assert await get_work_runner().resume_lost_messages(chat, topic_id=topic) == 0
    assert operations.count("send") == sends

    if mode == "ordinary-recovery":
        await runtime.stop_listening()  # This fixture's isolated native reader only.
        async with client.test_request_factory() as session:
            ordinary = await session.get(Block, ordinary_id)
            ordinary.created_at = datetime.now(UTC) - timedelta(hours=3)
            await session.commit()
        runtime = ClaudeCodeRuntime(channel)
        chat = ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(machine.workspace),
            compute=ComputePool([runtime], channel.name),
        )
        app.dependency_overrides[get_chat_service] = lambda: chat
        allow.set()
        assert await chat.recover_sessions() == 1
        await chat.replays_settled()
    else:
        allow.set()

    async with asyncio.timeout(60):
        while operations.count("send") == sends:
            for subscription in runtime.subscriptions.values():
                await subscription.drain()
            await asyncio.sleep(0.01)
        # Concurrent recovery scans must not send the deferred block again.
        await asyncio.gather(
            *(
                get_work_runner().resume_lost_messages(chat, topic_id=topic)
                for _ in range(4)
            )
        )
        while True:
            await settle_turn(chat, topic)
            async with client.test_request_factory() as session:
                row = await session.scalar(
                    select(NativeInput).where(
                        NativeInput.topic_id == topic,
                        NativeInput.work_id == ordinary_id,
                    )
                )
                if row is not None and row.completed_at:
                    break
            await asyncio.sleep(0.01)
    await get_work_runner().drain(10)
    async with client.test_request_factory() as session:
        rows = list(
            await session.scalars(
                select(NativeInput).where(
                    NativeInput.topic_id == topic,
                )
            )
        )
        assert len(rows) == 3
        assert all(
            row.echoed_at and row.settled_at and row.completed_at for row in rows
        )
        assert {row.native_session_id for row in rows} == {native}
        assert {row.recipient_handle for row in rows} == {recipient_handle}
        assert default_seat not in {row.recipient_handle for row in rows}
        owners = [row for row in rows if str(ordinary_id) in row.held_block_ids]
        assert len(owners) == 1
        owner = owners[0]
        assert owner.work_id == ordinary_id and owner.execution_work_id == ordinary_id
        assert str(ordinary_id) in owner.released_block_ids
        ordinary = await session.get(Block, ordinary_id)
        assert consumed_turn(ordinary) == str(ordinary_id)
        turns = list(
            await session.scalars(
                select(AgentTurn).where(
                    AgentTurn.topic_id == topic,
                )
            )
        )
        assert len(turns) == 3 and all(turn.stopped_at for turn in turns)
    assert operations.count("send") == sends + 1
    assert operations.count("steer") == 0
    assert native_runner.session_id == native
    assert native_runner.process is process and process.returncode is None
    return chat, runtime
