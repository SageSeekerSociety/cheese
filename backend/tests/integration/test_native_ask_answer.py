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
from app.domain.block.models import Block, consumed_turn
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


@pytest.mark.parametrize("mode", ["busy", "idle", "idle-race"])
def test_http_answer_continues_original_native_executor(client, tmp_path, mode):
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
        initial_sends = operations.count("send")
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
        if mode == "idle-race":
            assemble = chat._assemble_turn

            async def paused_assembly(**kwargs):
                prepared = await assemble(**kwargs)
                prepared_seen.set()
                await prepared_gate.wait()
                return prepared

            chat._assemble_turn = paused_assembly
            directive = headless_contract.do(
                "Bash",
                command=(
                    f"for i in $(seq 1 1200); do test -f '{gate}' && break; "
                    "sleep 0.05; done; printf ASK_HTTP_GATE"
                ),
                description="hold admitted answer work",
            )
            machine.server.state["actions"] = [
                lambda _body: json.loads(directive[3:]),
                None,
            ]
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

        async def verify():
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
            if mode == "idle-race":
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
            async with client.test_request_factory() as session:
                rows = list(
                    await session.scalars(
                        select(NativeInput).where(NativeInput.topic_id == topic)
                    )
                )
                assert len(rows) == (2 if mode == "idle" else 3)
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
                assert len(deliveries) == (1 if mode == "idle" else 2)
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
