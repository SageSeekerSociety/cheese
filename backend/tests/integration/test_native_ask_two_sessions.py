"""A real HTTP Ask answer resumes its native session while another seat works.

Only the two model providers and device discovery are fixture providers. Native
pipes/tools, authenticated group HTTP, delivery, receipts and PostgreSQL are real.
"""

import asyncio
import json
import sys
import uuid
from contextlib import ExitStack
from pathlib import Path

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.claude_code.protocol import INPUT_PROTOCOL
from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.agent.harness.claude_code.runtime import ClaudeCodeRuntime, Handle
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.models import Delivery, NativeInput
from app.main import app
from tests.conftest import settle_turn
from tests.integration.conftest import (
    chat_ws_url,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.unit.test_claude_runner import Machine


def test_http_group_answer_resumes_asking_session_while_other_native_session_is_busy(
    client, tmp_path
):
    scripts = str(Path(__file__).resolve().parents[3] / "scripts/remote_execution")
    sys.path.insert(0, scripts)
    import headless_contract

    machines, runners, handles, operations = {}, {}, {}, {}
    gate_a, gate_b = tmp_path / "release-a", tmp_path / "release-b"
    runtime = chat = topic = None
    resources = ExitStack()
    previous_chat = app.dependency_overrides.get(get_chat_service)

    class PlannedTools(headless_contract.Directives):
        def __init__(self):
            self.tools = {}

        def __getitem__(self, index):
            if index in self.tools:
                return lambda _body: self.tools[index]
            return super().__getitem__(index)

    class Channel:
        name = "native-ask-two-sessions"
        provisions_machine = False
        deferred_work = False
        builds_model_env = False

        def available(self):
            return True

        async def prepare_topic(self, **kwargs):
            return True, ""

        async def ensure(self, session, opening, live=None):
            seat = opening.agent_handle or session.agent_handle
            assert seat in machines
            if live is not None:
                return live
            if seat not in handles:
                machine = machines[seat]
                runner = runners[seat] = Runner(machine.state)
                native = await runner.start(
                    command=machine.command,
                    env=machine.env,
                    resume=None,
                    agent_handle=seat,
                )
                handles[seat] = Handle(
                    session,
                    "isolated-device",
                    str(machine.state),
                    native,
                    seat,
                    machine.root / "mirror.sqlite",
                    INPUT_PROTOCOL,
                    frozenset(runner.capabilities),
                )
            assert handles[seat].session == session
            return handles[seat]

        async def call(self, held, method, params):
            seat = held.agent_handle
            assert held is handles[seat]
            operations[seat].append(
                (method, params.get("input_id"), params.get("work_id"))
            )
            return await runners[seat].dispatch(method, params)

        async def discover(self, device_id):
            return list(handles.values())

        async def images(self, held, images):
            assert not images
            return []

    def input_calls(seat):
        return [
            call
            for call in operations[seat]
            if call[0] in ("send", "steer", "interrupt", "control", "command")
        ]

    try:
        project = post_project(
            client, {"name": "Native Ask competing sessions"}, owner="alice"
        ).json()["data"]
        project_id = uuid.UUID(project["id"])
        topic = uuid.UUID(project["root_topic_id"])
        seat_b = room_agent_seat(client, str(topic))
        made = client.post(f"/projects/{project_id}/agents", json={"handle": "opus"})
        assert made.status_code == 200, made.text
        seat_a = made.json()["data"]["seat_handle"]
        joined = client.post(
            f"/topics/{topic}/members",
            json={"handle": seat_a, "role": "member", "actor": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert joined.status_code == 200, joined.text
        assert seat_a != seat_b
        for seat, directory in ((seat_a, "a"), (seat_b, "b")):
            machines[seat] = Machine(headless_contract, tmp_path / directory)
            resources.callback(machines[seat].close)
            machines[seat].server.state["actions"] = PlannedTools()
            operations[seat] = []
        runtime = ClaudeCodeRuntime(Channel())
        chat = ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(machines[seat_a].workspace),
            compute=ComputePool([runtime], Channel.name),
        )
        app.dependency_overrides[get_chat_service] = lambda: chat

        def plan_tool(seat, directive):
            state = machines[seat].server.state
            state["actions"].tools[len(state["requests"])] = json.loads(directive[3:])

        def gated_command(gate, started, label):
            return headless_contract.do(
                "Bash",
                command=(
                    f"printf %s {label} > '{started}'; "
                    f"while ! test -f '{gate}'; do sleep 0.05; done; "
                    f"printf %s {label}"
                ),
                timeout=240000,
                description="hold this native session",
            )

        async def wait_active(seat, started, label):
            async with asyncio.timeout(30):
                while True:
                    if seat in handles:
                        runner = runners[seat]
                        assert runner.process.returncode is None
                        ping = await runner.dispatch("ping", {})
                        if (
                            started.is_file()
                            and started.read_text() == label
                            and ping["alive"]
                            and ping["session_id"] == runner.session_id
                            and ping["working"]
                            and ping["work_id"]
                        ):
                            work = uuid.UUID(ping["work_id"])
                            logical = handles[seat].session.agent_handle
                            active = chat._hook_work.get((topic, work))
                            if (
                                runtime.work_in_flight(topic, logical) == work
                                and chat.has_running_turn(topic, logical)
                                and work in chat._active_turn_ids.get(topic, set())
                                and active is not None
                                and active.agent_instance_handle == logical
                            ):
                                return work
                    await asyncio.sleep(0.05)

        started_a = machines[seat_a].workspace / "ask-origin-started.txt"
        started_b = machines[seat_b].workspace / "ask-other-started.txt"
        plan_tool(seat_a, gated_command(gate_a, started_a, "ASK_ORIGIN_GATE"))
        with client.websocket_connect(chat_ws_url(str(topic), "alice")):
            post_message(
                client,
                str(topic),
                "alice",
                {"content": f"<@{seat_a}> 等待用户回答后继续原执行。"},
            )
            original_work = client.portal.call(
                wait_active, seat_a, started_a, "ASK_ORIGIN_GATE"
            )
            runner_a = runners[seat_a]
            native_a, process_a = runner_a.session_id, runner_a.process
            asked = client.post(
                f"/topics/{topic}/asks",
                json={
                    "questions": [
                        {
                            "question": "是否继续原执行？",
                            "options": [{"text": "继续"}, {"text": "稍后"}],
                        }
                    ]
                },
                headers={
                    "X-Cheese-Token": mint_scoped_token(
                        project_id=str(project_id),
                        topic_id=str(topic),
                        agent_handle=seat_a,
                    )
                },
            )
            assert asked.status_code == 200, asked.text
            group = asked.json()["data"]
            question = group["blocks"][0]
            origin = question["meta"]["ask_origin"]
            assert origin["native_session_id"] == native_a
            assert origin["recipient_handle"] == seat_a
            assert uuid.UUID(origin["work_id"]) == original_work
            assert runner_a.work == str(original_work)
            gate_a.touch()
        # B has not started: the existing whole-topic helper can close only A.
        client.portal.call(settle_turn, chat, topic)
        assert not runner_a.working

        continued = machines[seat_a].workspace / "answer-owner.txt"
        continuation = headless_contract.do(
            "Bash",
            command=f"printf NATIVE_ASK_A_CONTINUED > '{continued}'; cat '{continued}'",
            description="continue the asking executor",
        )
        plan_tool(seat_a, continuation)
        plan_tool(seat_b, gated_command(gate_b, started_b, "ASK_OTHER_GATE"))
        with client.websocket_connect(chat_ws_url(str(topic), "alice")):
            post_message(
                client,
                str(topic),
                "alice",
                {"content": f"<@{seat_b}> 保持当前执行，等待放行。"},
            )
            other_work = client.portal.call(
                wait_active, seat_b, started_b, "ASK_OTHER_GATE"
            )
        runner_b = runners[seat_b]
        native_b, process_b, work_b = (
            runner_b.session_id,
            runner_b.process,
            runner_b.work,
        )
        assert native_a != native_b and process_a.pid != process_b.pid
        assert runner_b.working and not runner_a.working
        assert uuid.UUID(work_b) == other_work

        async def b_inputs():
            async with asyncio.timeout(10):
                while True:
                    await runtime.subscriptions[
                        (topic, handles[seat_b].session.agent_handle)
                    ].drain()
                    async with client.test_request_factory() as session:
                        rows = list(
                            await session.scalars(
                                select(NativeInput)
                                .where(
                                    NativeInput.topic_id == topic,
                                    NativeInput.recipient_handle == seat_b,
                                )
                                .order_by(NativeInput.id)
                            )
                        )
                        if rows:
                            assert len(rows) == 1
                            row = rows[0]
                            assert row.native_session_id == native_b
                            assert row.execution_work_id == uuid.UUID(work_b)
                            assert not row.completed_at
                            if row.echoed_at and row.settled_at:
                                return (
                                    row.id,
                                    row.input_id,
                                    row.work_id,
                                    row.execution_work_id,
                                    row.native_session_id,
                                    row.delivery_id,
                                    row.event_id,
                                    tuple(row.held_block_ids),
                                    tuple(row.released_block_ids),
                                    tuple(row.block_ids),
                                    tuple(row.seen_block_ids),
                                    row.seen_by,
                                    row.registered_at,
                                    row.accepted_at,
                                    row.echoed_at,
                                    row.settled_at,
                                    row.completed_at,
                                )
                    await asyncio.sleep(0.05)

        before_b = client.portal.call(b_inputs)
        before_b_calls, before_a_calls = input_calls(seat_b), input_calls(seat_a)
        answer = client.post(
            f"/topics/asks/{group['group']['id']}/settle",
            json={
                "topic_id": str(topic),
                "asked_by": seat_a,
                "client_op_id": "two-sessions-group-answer",
                "expect_version": 0,
                "answered": [
                    {
                        "block_id": question["id"],
                        "kind": "option",
                        "option": "继续",
                        "client_op_id": "two-sessions-answer",
                        "expect_version": 0,
                    }
                ],
                "later": [],
                "unanswered": [],
            },
            headers=session_auth_headers("alice"),
        )
        assert answer.status_code == 200, answer.text
        event_id = uuid.UUID(answer.json()["data"]["settlement"]["delivery_event_id"])

        async def wait_for_answer():
            async with asyncio.timeout(90):
                while True:
                    subscription = runtime.subscriptions[
                        (topic, handles[seat_a].session.agent_handle)
                    ]
                    await subscription.drain()
                    response = await asyncio.to_thread(
                        client.get,
                        f"/topics/asks/{group['group']['id']}",
                        params={"topic_id": str(topic), "asked_by": seat_a},
                        headers=session_auth_headers("alice"),
                    )
                    assert response.status_code == 200, response.text
                    current = response.json()["data"]
                    receipt = current["receipt"]
                    if (
                        receipt
                        and receipt["completed_at"]
                        and not runner_a.working
                        and runtime.work_in_flight(
                            topic, handles[seat_a].session.agent_handle
                        )
                        is None
                        and not chat.has_running_turn(
                            topic, handles[seat_a].session.agent_handle
                        )
                    ):
                        return current
                    await asyncio.sleep(0.05)

        current = client.portal.call(wait_for_answer)
        assert current["blocks"][0]["meta"]["ask_origin"] == origin
        assert current["receipt"]["event_id"] == str(event_id)
        assert current["receipt"]["state"] == "received"
        assert current["receipt"]["received_at"] and current["receipt"]["completed_at"]
        assert continued.read_text() == "NATIVE_ASK_A_CONTINUED"
        assert not (machines[seat_b].workspace / continued.name).exists()
        assert runner_a.session_id == native_a and runner_a.process is process_a
        assert process_a.returncode is None
        assert runner_b.session_id == native_b and runner_b.process is process_b
        assert process_b.returncode is None and runner_b.working
        assert runner_b.work == work_b

        async def still_busy_b():
            ping = await runner_b.dispatch("ping", {})
            logical = handles[seat_b].session.agent_handle
            assert ping["alive"] and ping["working"]
            assert ping["session_id"] == native_b and ping["work_id"] == work_b
            assert runtime.work_in_flight(topic, logical) == other_work
            assert chat.has_running_turn(topic, logical)
            assert other_work in chat._active_turn_ids[topic]
            assert chat._hook_work[(topic, other_work)].agent_instance_handle == logical

        client.portal.call(still_busy_b)
        assert input_calls(seat_b) == before_b_calls
        assert [call[0] for call in input_calls(seat_a)[len(before_a_calls) :]] == [
            "send"
        ]
        assert client.portal.call(b_inputs) == before_b

        async def ownership():
            async with client.test_request_factory() as session:
                deliveries = list(
                    await session.scalars(
                        select(Delivery).where(
                            Delivery.topic_id == topic, Delivery.event_id == event_id
                        )
                    )
                )
                assert len(deliveries) == 1
                delivery = deliveries[0]
                assert (
                    delivery.recipient_handle == seat_a and delivery.state == "received"
                )
                assert delivery.sent_at and delivery.payload["ask_origin"] == origin
                wake = await session.scalar(
                    select(Block).where(
                        Block.topic_id == topic,
                        Block.meta["delivery_event_id"].as_string() == str(event_id),
                    )
                )
                assert wake is not None
                assert wake.meta["answer_group"] == group["group"]["id"]
                assert (
                    wake.meta["agent_recipient"]["handle"]
                    == handles[seat_a].session.agent_handle
                )
                rows = list(
                    await session.scalars(
                        select(NativeInput).where(NativeInput.topic_id == topic)
                    )
                )
                assert sum(row.recipient_handle == seat_a for row in rows) == 2
                assert sum(row.recipient_handle == seat_b for row in rows) == 1
                original = [
                    row
                    for row in rows
                    if row.recipient_handle == seat_a and row.work_id == original_work
                ]
                assert len(original) == 1
                assert original[0].native_session_id == native_a
                assert original[0].execution_work_id == original_work
                assert original[0].echoed_at and original[0].settled_at
                assert original[0].completed_at
                owners = [row for row in rows if str(wake.id) in row.held_block_ids]
                assert len(owners) == 1
                owner = owners[0]
                assert owner.recipient_handle == seat_a
                assert owner.native_session_id == native_a
                assert owner.delivery_id == delivery.id and owner.event_id == event_id
                assert owner.echoed_at and owner.settled_at and owner.completed_at
                assert owner.work_id == owner.execution_work_id != original_work
                assert current["receipt"]["received_at"] == owner.settled_at.isoformat()
                assert (
                    current["receipt"]["completed_at"] == owner.completed_at.isoformat()
                )
                assert str(wake.id) in owner.released_block_ids
                assert consumed_turn(wake) == str(owner.execution_work_id)
                return str(owner.input_id), str(owner.execution_work_id)

        answered_input, answered_work = client.portal.call(ownership)
        records = client.portal.call(runner_a.journal.read)
        echoes = [
            row["record"]
            for row in records
            if row["record"].get("uuid") == answered_input
            and (row["record"].get("cheese") or {}).get("receipt")
        ]
        assert len(echoes) == 1
        assert echoes[0]["cheese"]["receipt_session_id"] == native_a
        assert echoes[0]["cheese"]["receipt_work_id"] == answered_work
        assert echoes[0]["cheese"]["receipt_execution_work_id"] == answered_work
        assert any(
            block.get("type") == "tool_result"
            and "NATIVE_ASK_A_CONTINUED" in headless_contract.text_of(block)
            for row in records
            if row["record"].get("cheese", {}).get("work_id") == answered_work
            for block in headless_contract.blocks(row["record"])
        )
    finally:
        gate_a.touch()
        gate_b.touch()
        try:
            if runtime is not None:
                try:
                    if chat is not None and topic is not None:
                        client.portal.call(settle_turn, chat, topic)
                finally:
                    client.portal.call(runtime.stop_listening)
        finally:
            try:

                async def close_runners():
                    results = await asyncio.gather(
                        *(runner.close() for runner in runners.values()),
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, BaseException):
                            raise result

                client.portal.call(close_runners)
            finally:
                try:
                    resources.close()
                finally:
                    if previous_chat is None:
                        app.dependency_overrides.pop(get_chat_service, None)
                    else:
                        app.dependency_overrides[get_chat_service] = previous_chat
                    sys.path.remove(scripts)
