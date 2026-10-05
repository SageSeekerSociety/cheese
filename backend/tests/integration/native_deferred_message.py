"""New native continuation checks, separate from closed admission checkpoints."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
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
    take_recovery,
):
    import app.domain.agent.chat as chat_module
    import app.domain.agent.room.turn as turn_module
    from app.domain.agent import pending_messages

    monkeypatch.setattr(pending_messages, "_runner", get_work_runner())
    assert pending_messages.current_runner() is get_work_runner()

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
                NativeInput.conversation_id == topic,
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
        runtime = channel.next_process()
        take_recovery(chat, runtime)
        chat = ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(machine.workspace),
            compute=ComputePool([runtime], channel.name),
        )
        take_recovery(chat, runtime)
        app.dependency_overrides[get_chat_service] = lambda: chat
        allow.set()
        assert await chat.recover_sessions() == 1
        await chat.replays_settled()
    elif mode in ("ordinary-removed", "ordinary-prepare-removed"):
        runner = get_work_runner()
        queued, released = asyncio.Event(), asyncio.Event()
        if mode == "ordinary-removed":
            admit = runner._admit

            async def paused_admit(service, room, turn):
                if turn == ordinary_id:
                    queued.set()
                    await released.wait()
                return await admit(service, room, turn)

            monkeypatch.setattr(runner, "_admit", paused_admit)
        else:
            from app.domain.agent_instance.services import AgentInstanceService

            assemble = chat._assemble_turn
            lookup = AgentInstanceService.get_in_project
            held_blocks = turn_module.held_blocks
            held_seats = []
            preparing = None

            async def tracked_assembly(**kwargs):
                nonlocal preparing
                if kwargs["turn_id"] == ordinary_id:
                    preparing = asyncio.current_task()
                try:
                    return await assemble(**kwargs)
                finally:
                    preparing = None

            async def paused_lookup(service, **kwargs):
                # The explicit-instance roster check has returned; acting-seat
                # selection and the final preparation check have not run yet.
                if asyncio.current_task() is preparing:
                    queued.set()
                    await released.wait()
                return await lookup(service, **kwargs)

            async def observed_holds(session, **kwargs):
                if asyncio.current_task() is preparing:
                    held_seats.append(kwargs["recipient_handle"])
                return await held_blocks(session, **kwargs)

            monkeypatch.setattr(chat, "_assemble_turn", tracked_assembly)
            monkeypatch.setattr(AgentInstanceService, "get_in_project", paused_lookup)
            monkeypatch.setattr(turn_module, "held_blocks", observed_holds)
        allow.set()
        async with asyncio.timeout(30):
            while not queued.is_set():
                await asyncio.sleep(0.01)
        try:
            from tests.integration.conftest import session_auth_headers

            removed = await asyncio.to_thread(
                client.delete,
                f"/topics/{topic}/members/{recipient_handle}?actor=alice",
                headers=session_auth_headers("alice"),
            )
            assert removed.status_code == 200, removed.text
        finally:
            released.set()
        await runner.drain(10)
        await settle_turn(chat, topic)
        if mode == "ordinary-prepare-removed":
            assert held_seats == [recipient_handle]
        async with client.test_request_factory() as session:
            rows = list(
                await session.scalars(
                    select(NativeInput).where(NativeInput.conversation_id == topic)
                )
            )
            assert len(rows) == 2 and all(row.completed_at for row in rows)
            assert default_seat not in {row.recipient_handle for row in rows}
            ordinary = await session.get(Block, ordinary_id)
            assert consumed_turn(ordinary) is None and prompt_attempts(ordinary) == 0
            turn = await session.get(AgentTurn, ordinary_id)
            if mode == "ordinary-prepare-removed":
                assert turn is not None and turn.stopped_at is not None
                assert turn.delivered_at is None and turn.agent_handle is None
            else:
                assert turn is None
        assert operations.count("send") == sends and operations.count("steer") == 0
        assert native_runner.session_id == native and native_runner.process is process
        return chat, runtime
    else:
        allow.set()

    async with asyncio.timeout(60):
        while operations.count("send") == sends:
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
                        NativeInput.conversation_id == topic,
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
                    NativeInput.conversation_id == topic,
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
                    AgentTurn.conversation_id == topic,
                )
            )
        )
        assert len(turns) == 3 and all(turn.stopped_at for turn in turns)
    assert operations.count("send") == sends + 1
    assert operations.count("steer") == 0
    assert native_runner.session_id == native
    assert native_runner.process is process and process.returncode is None
    return chat, runtime
