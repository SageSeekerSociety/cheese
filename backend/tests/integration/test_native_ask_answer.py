"""Real Ask HTTP answers return to the native executor that asked.

The local provider is deterministic and discovery is a saved Handle. HTTP actor
resolution, durable delivery admission, native pipes and PostgreSQL settlement
are real. This does not cover runner upgrades or production device discovery.
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.api.deps import get_chat_service, get_work_runner
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.claude_code.protocol import INPUT_PROTOCOL
from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.agent.harness.claude_code.runtime import ClaudeCodeRuntime, Handle
from app.domain.agent.models import AgentTurn
from app.domain.agent.runtime import addressed_to_agent
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.receipts import held_blocks
from app.main import app
from tests.conftest import settle_turn
from tests.integration.conftest import (
    chat_ws_url,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_claude_session_records import _until
from tests.unit.test_claude_runner import Machine


@pytest.mark.parametrize(
    "mode",
    [
        "busy",
        "idle",
        "idle-race",
        "accepted-start",
        "project-seat",
        "history",
        "history-pruned",
        "ordinary-start",
        "ordinary-resume",
        "ordinary-recovery",
        "ordinary-removed",
        "history-multi",
        "history-multi-session",
        "history-multi-missing",
    ],
)
def test_http_answer_continues_original_native_executor(
    client, tmp_path, mode, monkeypatch
):
    scripts = str(Path(__file__).resolve().parents[3] / "scripts/remote_execution")
    sys.path.insert(0, scripts)
    import headless_contract

    machine = None
    native_runner = None
    handle = None
    operations = []

    class Channel:
        name = "native-ask-fixture"
        provisions_machine = False
        deferred_work = False
        builds_model_env = False

        def available(self):
            return True

        async def prepare_topic(self, **kwargs):
            return True, ""

        async def ensure(self, session, opening):
            nonlocal native_runner, handle
            if native_runner is None:
                native_runner = Runner(machine.state)
                native = await native_runner.start(
                    command=machine.command,
                    env=machine.env,
                    resume=None,
                    agent_handle=opening.agent_handle or session.agent_handle,
                )
                handle = Handle(
                    session,
                    "isolated-device",
                    str(machine.state),
                    native,
                    opening.agent_handle or session.agent_handle,
                    tmp_path / "mirror.sqlite",
                    INPUT_PROTOCOL,
                )
            assert session == handle.session
            return handle

        async def call(self, held, method, params):
            assert held is handle
            operations.append(method)
            return await native_runner.dispatch(method, params)

        async def discover(self, device_id):
            return [handle] if handle is not None else []

        async def images(self, held, images):
            assert not images
            return []

    channel = Channel()
    runtime = ClaudeCodeRuntime(channel)
    gate = tmp_path / "continue-ask"
    try:
        project = post_project(
            client, {"name": "Native Ask answer", "owner_handle": "alice"}
        ).json()["data"]
        project_id = uuid.UUID(project["id"])
        topic = uuid.UUID(project["root_topic_id"])
        default_seat = room_agent_seat(client, str(topic))
        made = client.post(f"/projects/{project_id}/agents", json={"handle": "opus"})
        assert made.status_code == 200, made.text
        asker = made.json()["data"]["seat_handle"]
        joined = client.post(
            f"/topics/{topic}/members",
            json={"handle": asker, "role": "member", "actor": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert joined.status_code == 200, joined.text
        assert asker != default_seat
        machine = Machine(headless_contract, tmp_path)
        chat = ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(machine.workspace),
            compute=ComputePool([runtime], channel.name),
        )
        app.dependency_overrides[get_chat_service] = lambda: chat
        if mode == "busy":
            content = headless_contract.do(
                "Bash",
                command=(
                    f"for i in $(seq 1 1200); do test -f '{gate}' && break; "
                    "sleep 0.05; done; printf ASK_HTTP_GATE"
                ),
                description="wait for answer",
            )
        else:
            content = "先保留这个会话，等我回答。"
        with client.websocket_connect(chat_ws_url(str(topic), "alice")) as ws:
            ws.send_json({"type": "message", "content": f"<@{asker}> " + content})
            observed = _until(
                ws,
                lambda frame: (
                    frame["type"] == "error"
                    or (
                        mode == "busy"
                        and frame["type"] == "event_block"
                        and "ASK_HTTP_GATE" in str(frame["block"])
                    )
                    or (mode != "busy" and frame["type"] == "done")
                ),
            )
            assert observed["type"] != "error", observed
        if mode != "busy":
            client.portal.call(settle_turn, chat, topic)

        async def initial_work():
            async with client.test_request_factory() as session:
                return await session.scalar(
                    select(AgentTurn.id).where(AgentTurn.topic_id == topic)
                )

        native, original_work, process = (
            native_runner.session_id,
            client.portal.call(initial_work),
            native_runner.process,
        )
        if mode.startswith("history-multi"):
            from app.domain.agent.harness import Opening
            from app.domain.agent.harness.claude_code.journal import Journal
            from app.domain.agent.harness.driven import runtime as driven_runtime
            from app.domain.delivery.input_identity import InputEffects

            high = uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff")
            low = uuid.UUID("00000000-0000-4000-8000-000000000001")
            directive = headless_contract.do(
                "Bash",
                command=(
                    f"for i in $(seq 1 1200); do test -f '{gate}' && break; "
                    "sleep 0.05; done; printf ASK_MULTI_HISTORY"
                ),
                description="hold two-input execution",
            )
            machine.server.state["actions"] = [
                *([None] * len(machine.server.state["requests"])),
                lambda _body: json.loads(directive[3:]),
                None,
            ]

            async def prepare_multi():
                ref = handle.session
                registrar = chat._input_registrar(InputEffects())
                await runtime.send(
                    ref,
                    directive,
                    Opening(
                        system_prompt="你是芝士。", agent_handle=handle.agent_handle
                    ),
                    work_id=high,
                    on_mark=lambda _work: None,
                    register_input=registrar,
                )
                async with asyncio.timeout(30):
                    while not native_runner.working:
                        await asyncio.sleep(0.01)
                real_uuid4 = uuid.uuid4
                try:
                    # Change only the newly constructed steer's UUID. Runner
                    # admission, pipes, echoes and result remain unmodified.
                    monkeypatch.setattr(driven_runtime.uuid, "uuid4", lambda: low)
                    assert await runtime.deliver(
                        topic,
                        "同轮追加输入",
                        expected_work_id=high,
                        agent_handle=ref.agent_handle,
                        register_input=registrar,
                    )
                finally:
                    monkeypatch.setattr(driven_runtime.uuid, "uuid4", real_uuid4)
                gate.touch()
                async with asyncio.timeout(90):
                    while native_runner.working:
                        await asyncio.sleep(0.05)
                await settle_turn(chat, topic)
                await runtime.stop_listening()
                async with client.test_request_factory() as session:
                    rows = list(
                        await session.scalars(
                            select(NativeInput).where(
                                NativeInput.topic_id == topic,
                                NativeInput.execution_work_id == high,
                            )
                        )
                    )
                    assert {row.input_id for row in rows} == {high, low}
                    assert all(row.completed_at for row in rows)
                    for row in rows:
                        row.completed_at = None
                    await session.commit()

            client.portal.call(prepare_multi)
            journal = Journal(handle.mirror)
            try:
                records = []
                after = 0
                while page := journal.read(after):
                    records.extend(page)
                    after = page[-1]["sequence"]
                work_records = [
                    entry
                    for entry in records
                    if (entry["record"].get("cheese") or {}).get("work_id") == str(high)
                ]
                echoes = [
                    entry["record"]["uuid"]
                    for entry in work_records
                    if (entry["record"].get("cheese") or {}).get("receipt")
                ]
                assert echoes == [str(high), str(low)]
                result = next(
                    entry
                    for entry in work_records
                    if entry["record"].get("type") == "result"
                )
                assert result["record"]["cheese"]["completion_input_ids"] == [
                    str(low),
                    str(high),
                ]
                landed = journal.recall("landed")
                assert int(landed) >= result["sequence"]
                if mode != "history-multi":
                    # Corrupt just the retained result, not native execution.
                    mutated = result["record"]
                    if mode == "history-multi-session":
                        mutated["cheese"]["completion_session_id"] = "foreign-session"
                    else:
                        mutated["cheese"]["completion_input_ids"] = [str(low)]
                    with journal.connection:
                        journal.connection.execute(
                            "UPDATE records SET record=? WHERE sequence=?",
                            (json.dumps(mutated), result["sequence"]),
                        )
            finally:
                journal.close()
            runtime = ClaudeCodeRuntime(channel)
            chat = ChatService(
                session_factory=client.test_request_factory,
                base_system_prompt="你是芝士。",
                workspace_root=str(machine.workspace),
                compute=ComputePool([runtime], channel.name),
            )
            app.dependency_overrides[get_chat_service] = lambda: chat
            assert client.portal.call(chat.recover_sessions) == 1
            client.portal.call(chat.replays_settled)

            async def multi_recovery_state():
                from app.domain.delivery.answer_ownership import (
                    seat_has_unfinished_input,
                )

                refusal = None
                if mode != "history-multi":
                    try:
                        await runtime.replay(handle.session, known_texts=set())
                    except ValueError as exc:
                        refusal = str(exc)
                async with client.test_request_factory() as session:
                    rows = list(
                        await session.scalars(
                            select(NativeInput).where(
                                NativeInput.topic_id == topic,
                                NativeInput.execution_work_id == high,
                            )
                        )
                    )
                    completed = [bool(row.completed_at) for row in rows]
                    unfinished = await seat_has_unfinished_input(
                        session, topic, handle.agent_handle
                    )
                return completed, unfinished, refusal

            completed, unfinished, refusal = client.portal.call(multi_recovery_state)
            assert completed == [mode == "history-multi"] * 2
            assert unfinished == (mode != "history-multi")
            if mode == "history-multi-missing":
                assert refusal == "Historical completion disagrees with retained inputs"
            else:
                # A foreign session lacks trusted interval identity. It remains
                # quarantined without throwing or fabricating completion.
                assert refusal is None
            journal = Journal(handle.mirror)
            try:
                assert journal.recall("landed") == landed
            finally:
                journal.close()
            assert native_runner.process is process
            assert operations.count("send") == 2 and operations.count("steer") == 1
            return
        initial_sends = operations.count("send")
        if mode in ("history", "history-pruned"):
            from app.domain.agent.harness.claude_code.journal import Journal
            from app.domain.delivery.answer_ownership import seat_has_unfinished_input

            async def old_completion():
                async with client.test_request_factory() as session:
                    rows = list(
                        await session.scalars(
                            select(NativeInput).where(NativeInput.topic_id == topic)
                        )
                    )
                    assert len(rows) == 1 and rows[0].completed_at
                    rows[0].completed_at = None  # Exact post-upgrade legacy NULL row.
                    await session.commit()
                    assert await seat_has_unfinished_input(
                        session, topic, handle.agent_handle
                    )
                await runtime.stop_listening()

            client.portal.call(old_completion)
            journal = Journal(handle.mirror)
            try:
                landed_before = journal.recall("landed")
                assert landed_before and int(landed_before) > 0
                if mode == "history-pruned":
                    journal.prune("9999-01-01T00:00:00Z")
            finally:
                journal.close()
            runtime = ClaudeCodeRuntime(channel)
            chat = ChatService(
                session_factory=client.test_request_factory,
                base_system_prompt="你是芝士。",
                workspace_root=str(machine.workspace),
                compute=ComputePool([runtime], channel.name),
            )
            app.dependency_overrides[get_chat_service] = lambda: chat
            assert client.portal.call(chat.recover_sessions) == 1
            client.portal.call(chat.replays_settled)

            async def verify_history():
                async with client.test_request_factory() as session:
                    row = await session.scalar(
                        select(NativeInput).where(NativeInput.topic_id == topic)
                    )
                    assert bool(row.completed_at) == (mode == "history")
                    assert await seat_has_unfinished_input(
                        session, topic, handle.agent_handle
                    ) == (mode == "history-pruned")
                assert operations.count("send") == initial_sends
                assert operations.count("steer") == 0

            client.portal.call(verify_history)
            journal = Journal(handle.mirror)
            try:
                assert journal.recall("landed") == landed_before
            finally:
                journal.close()
        asked = client.post(
            f"/topics/{topic}/ask",
            json={
                "question": "接着执行哪个方案？",
                "options": [{"text": "继续"}, {"text": "稍后"}],
            },
            headers={
                "X-Cheese-Token": mint_scoped_token(
                    project_id=str(project_id),
                    topic_id=str(topic),
                    agent_handle=handle.agent_handle,
                )
            },
        )
        assert asked.status_code == 200, asked.text
        question = asked.json()["data"]
        assert question["author"] == handle.agent_handle
        prepared_gate = asyncio.Event()
        prepared_seen = asyncio.Event()
        delayed_writes = []
        if mode in ("idle-race", "accepted-start", "project-seat") or mode.startswith(
            "ordinary-"
        ):
            assemble = chat._assemble_turn

            async def paused_assembly(**kwargs):
                prepared = await assemble(**kwargs)
                prepared_seen.set()
                await prepared_gate.wait()
                return prepared

            if mode == "idle-race":
                chat._assemble_turn = paused_assembly
            elif mode == "accepted-start" or mode.startswith("ordinary-"):
                write = native_runner._write

                async def accepted_write(message):
                    # Model start is held after the real native admission ledger.
                    # Only this isolated process's stdin write is delayed.
                    delayed_writes.append(message)

                native_runner._write = accepted_write
            directive = headless_contract.do(
                "Bash",
                command=(
                    f"for i in $(seq 1 1200); do test -f '{gate}' && break; "
                    "sleep 0.05; done; printf ASK_HTTP_GATE"
                ),
                description="hold admitted answer work",
            )
            machine.server.state["actions"] = [
                *([None] * len(machine.server.state["requests"])),
                lambda _body: json.loads(directive[3:]),
                None,
            ]
        if mode == "project-seat":
            import app.domain.agent.runtime as work_runtime

            runner = get_work_runner()
            opener = work_runtime._open_turn
            normal_turn = uuid.uuid4()
            normal_opened, normal_release = asyncio.Event(), asyncio.Event()
            answer_queued = asyncio.Event()
            admit = runner._admit

            async def pause_open(factory, **fields):
                if fields["turn_id"] == normal_turn:
                    normal_opened.set()
                    await normal_release.wait()
                await opener(factory, **fields)

            async def observed_admit(service, room, turn):
                if turn != normal_turn:
                    answer_queued.set()
                return await admit(service, room, turn)

            async def policy(_topic):
                return {
                    "project_id": project_id,
                    "max_concurrent_turns": 1,
                    "credits_exhausted": False,
                }

            monkeypatch.setattr(work_runtime, "_open_turn", pause_open)
            monkeypatch.setattr(runner, "_admit", observed_admit)
            monkeypatch.setattr(chat, "work_policy", policy)

            async def start_normal():
                runner.submit(
                    chat,
                    topic,
                    author="system",
                    content="continue original session",
                    addressed=addressed_to_agent(asker),
                    turn_id=normal_turn,
                    recipient_instance_id=uuid.UUID(made.json()["data"]["id"]),
                )
                async with asyncio.timeout(10):
                    await normal_opened.wait()

            client.portal.call(start_normal)
        answer = client.post(
            f"/topics/blocks/{question['id']}/answer",
            json={
                "kind": "option",
                "option": "继续",
                "client_op_id": f"http-{mode}",
                "expect_version": 0,
            },
            headers=session_auth_headers("alice"),
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["data"]["meta"]["answer_log"][-1]["by"] == "alice"

        def take_recovery(recovered_chat, recovered_runtime):
            nonlocal chat, runtime
            chat, runtime = recovered_chat, recovered_runtime

        async def verify():
            nonlocal chat, runtime
            if mode == "history-pruned":
                await get_work_runner().drain(10)
                assert operations.count("send") == initial_sends
                assert operations.count("steer") == 0
                async with client.test_request_factory() as session:
                    delivery = await session.scalar(
                        select(Delivery).where(Delivery.topic_id == topic)
                    )
                    assert delivery.state == "pending" and delivery.sent_at is None
                    row = await session.scalar(
                        select(NativeInput).where(NativeInput.topic_id == topic)
                    )
                    assert row.completed_at is None
                return
            if mode.startswith("ordinary-"):
                await get_work_runner().drain(10)
                assert len(delayed_writes) == 1 and not native_runner.working
                async with client.test_request_factory() as session:
                    waiting = await session.scalar(
                        select(NativeInput).where(
                            NativeInput.topic_id == topic,
                            NativeInput.completed_at.is_(None),
                        )
                    )
                    assert waiting.accepted_at and waiting.echoed_at is None
                    held_work = waiting.work_id

                def post_ordinary():
                    with client.websocket_connect(
                        chat_ws_url(str(topic), "alice")
                    ) as ws:
                        ws.send_json(
                            {"type": "message", "content": f"<@{asker}> 普通追加消息"}
                        )
                        return _until(ws, lambda frame: frame["type"] == "user_block")

                await asyncio.to_thread(post_ordinary)
                await get_work_runner().drain(10)
                assert len(delayed_writes) == 1
                assert operations.count("send") == initial_sends + 1
                async with client.test_request_factory() as session:
                    assert (
                        len(
                            list(
                                await session.scalars(
                                    select(AgentTurn).where(AgentTurn.topic_id == topic)
                                )
                            )
                        )
                        == 2
                    )
                    ordinary = await session.scalar(
                        select(Block).where(
                            Block.topic_id == topic,
                            Block.content.contains("普通追加消息"),
                        )
                    )
                    assert ordinary and consumed_turn(ordinary) is None
                native_runner._write = write
                await write(delayed_writes[0])
                async with asyncio.timeout(30):
                    while not native_runner.working:
                        await asyncio.sleep(0.01)
                assert native_runner.work == str(held_work)
                if mode != "ordinary-start":
                    from tests.integration.native_deferred_message import (
                        finish_deferred_message,
                    )

                    chat, runtime = await finish_deferred_message(
                        client=client,
                        chat=chat,
                        runtime=runtime,
                        channel=channel,
                        native_runner=native_runner,
                        machine=machine,
                        operations=operations,
                        ordinary_id=ordinary.id,
                        held_work=held_work,
                        gate=gate,
                        mode=mode,
                        monkeypatch=monkeypatch,
                        topic=topic,
                        default_seat=default_seat,
                        recipient_handle=handle.agent_handle,
                        take_recovery=take_recovery,
                    )
                    return
            if mode == "project-seat":
                async with asyncio.timeout(10):
                    await answer_queued.wait()
                normal_release.set()
                async with asyncio.timeout(30):
                    while not native_runner.working:
                        await asyncio.sleep(0.01)
                assert native_runner.work == str(normal_turn)
            if mode == "accepted-start":
                await get_work_runner().drain(10)
                assert len(delayed_writes) == 1
                assert not native_runner.working
                async with client.test_request_factory() as session:
                    registered = list(
                        await session.scalars(
                            select(NativeInput).where(NativeInput.topic_id == topic)
                        )
                    )
                    waiting = next(
                        row for row in registered if row.completed_at is None
                    )
                    assert waiting.accepted_at and not waiting.echoed_at
                    held_work = waiting.work_id
                correction = await asyncio.to_thread(
                    client.post,
                    f"/topics/blocks/{question['id']}/answer",
                    json={
                        "kind": "option",
                        "option": "稍后",
                        "client_op_id": "http-correct-start",
                        "expect_version": 1,
                    },
                    headers=session_auth_headers("alice"),
                )
                assert correction.status_code == 200, correction.text
                await get_work_runner().drain(10)
                assert len(delayed_writes) == 1
                assert operations.count("send") == initial_sends + 1
                assert operations.count("steer") == 0
                native_runner._write = write
                await write(delayed_writes[0])
                async with asyncio.timeout(30):
                    while not native_runner.working:
                        await asyncio.sleep(0.01)
                assert native_runner.work == str(held_work)
                async with client.test_request_factory() as session:
                    deliveries = await session.scalars(
                        select(Delivery).where(
                            Delivery.topic_id == topic, Delivery.state == "pending"
                        )
                    )
                    for delivery in deliveries:
                        delivery.retry_at = None
                    await session.commit()
                await dispatch_pending(
                    client.test_request_factory, chat=chat, runner=get_work_runner()
                )
            if mode == "idle-race":
                async with asyncio.timeout(30):
                    await prepared_seen.wait()
                assert not chat._hook_work
                correction = await asyncio.to_thread(
                    client.post,
                    f"/topics/blocks/{question['id']}/answer",
                    json={
                        "kind": "option",
                        "option": "稍后",
                        "client_op_id": "http-correct-race",
                        "expect_version": 1,
                    },
                    headers=session_auth_headers("alice"),
                )
                assert correction.status_code == 200, correction.text
                await asyncio.sleep(0.05)
                assert operations.count("send") == initial_sends
                prepared_gate.set()
            async with asyncio.timeout(30):
                while (
                    operations.count("send") + operations.count("steer")
                    == initial_sends
                ):
                    await asyncio.sleep(0.05)
            if mode == "busy":
                assert operations.count("send") == initial_sends, operations
                assert operations.count("steer") == 1, operations
                assert native_runner.work == str(original_work)
            else:
                assert operations.count("send") == initial_sends + 1, operations
                if mode == "idle":
                    assert operations.count("steer") == 0, operations
            if mode == "busy":
                await get_work_runner().drain(10)
                retry = await asyncio.to_thread(
                    client.post,
                    f"/topics/blocks/{question['id']}/answer",
                    json={
                        "kind": "option",
                        "option": "继续",
                        "client_op_id": f"http-{mode}",
                        "expect_version": 0,
                    },
                    headers=session_auth_headers("alice"),
                )
                assert retry.status_code == 200, retry.text
                assert operations.count("steer") == 1
                correction = await asyncio.to_thread(
                    client.post,
                    f"/topics/blocks/{question['id']}/answer",
                    json={
                        "kind": "option",
                        "option": "稍后",
                        "client_op_id": "http-correct",
                        "expect_version": 1,
                    },
                    headers=session_auth_headers("alice"),
                )
                assert correction.status_code == 200, correction.text
                assert len(correction.json()["data"]["meta"]["answer_log"]) == 2
                async with asyncio.timeout(30):
                    while operations.count("steer") != 2:
                        await asyncio.sleep(0.05)
                assert operations.count("send") == initial_sends
            if mode in ("idle-race", "accepted-start"):
                async with asyncio.timeout(30):
                    while operations.count("steer") != 1:
                        await asyncio.sleep(0.05)
                assert operations.count("send") == initial_sends + 1, operations
            gate.touch()
            async with asyncio.timeout(90):
                while native_runner.working:
                    await asyncio.sleep(0.05)
            await settle_turn(chat, topic)
            await get_work_runner().drain(10)
            if mode == "project-seat":
                async with client.test_request_factory() as session:
                    pending = await session.scalars(
                        select(Delivery).where(
                            Delivery.topic_id == topic, Delivery.state == "pending"
                        )
                    )
                    for delivery in pending:
                        delivery.retry_at = None
                    await session.commit()
                await dispatch_pending(
                    client.test_request_factory, chat=chat, runner=get_work_runner()
                )
                await get_work_runner().drain(10)
                assert operations.count("send") == initial_sends + 1
                assert operations.count("steer") == 0
            async with client.test_request_factory() as session:
                rows = list(
                    await session.scalars(
                        select(NativeInput).where(NativeInput.topic_id == topic)
                    )
                )
                assert len(rows) == (
                    2
                    if mode in ("idle", "project-seat", "history", "ordinary-start")
                    else 3
                )
                assert {row.native_session_id for row in rows} == {native}
                assert all(row.echoed_at and row.settled_at for row in rows)
                assert all(
                    set(row.held_block_ids) <= set(row.released_block_ids)
                    for row in rows
                )
                assert not await held_blocks(
                    session,
                    project_id=project_id,
                    topic_id=topic,
                    recipient_handle=handle.agent_handle,
                )
                deliveries = list(
                    await session.scalars(
                        select(Delivery).where(Delivery.topic_id == topic)
                    )
                )
                assert len(deliveries) == (
                    1
                    if mode in ("idle", "project-seat", "history", "ordinary-start")
                    else 2
                )
                assert all(d.state == "received" and d.sent_at for d in deliveries)
                answers = list(
                    await session.scalars(
                        select(Block).where(
                            Block.topic_id == topic,
                            Block.meta["answer_to"].as_string() == question["id"],
                        )
                    )
                )
                assert len(answers) == len(deliveries)
                for answer_block in answers:
                    owners = [
                        row
                        for row in rows
                        if str(answer_block.id) in row.held_block_ids
                    ]
                    assert len(owners) == 1
                    assert str(answer_block.id) in owners[0].released_block_ids
                    assert consumed_turn(answer_block) == str(
                        owners[0].execution_work_id
                    )
                    assert (
                        answer_block.meta["agent_recipient"]["handle"]
                        == handle.session.agent_handle
                    )
                    assert answer_block.meta["agent_recipient"]["mentioned"]
                    if mode == "project-seat":
                        assert owners[0].delivery_id is None
                    else:
                        assert owners[0].delivery_id in {d.id for d in deliveries}
                turns = list(
                    await session.scalars(
                        select(AgentTurn).where(AgentTurn.topic_id == topic)
                    )
                )
                assert len(turns) == (1 if mode == "busy" else 2)
                if mode == "busy":
                    assert {row.execution_work_id for row in rows} == {original_work}
            assert native_runner.session_id == native
            assert native_runner.process is process and process.returncode is None

        client.portal.call(verify)
    finally:
        gate.touch()
        if "prepared_gate" in locals():
            client.portal.call(prepared_gate.set)
        if "normal_release" in locals():
            client.portal.call(normal_release.set)
        try:
            if native_runner is not None:
                client.portal.call(native_runner.close)
        finally:
            try:
                client.portal.call(runtime.stop_listening)
            finally:
                try:
                    if machine is not None:
                        machine.close()
                finally:
                    sys.path.remove(scripts)
