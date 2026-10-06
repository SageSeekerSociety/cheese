"""A new full backend-service interpreter resumes a retained native executor.

Only isolated test clients/readers are replaced. The native runner archive stays
alive on its Unix socket; discovery is a saved fixture descriptor, not a device.
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.main import app
from tests.conftest import settle_turn
from tests.integration.conftest import (
    add_external_member,
    chat_ws_url,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_claude_session_records import _until
from tests.integration.test_native_batch_ownership import _blocks
from tests.support.seat_channel import SeatChannel
from tests.unit.test_claude_runner import Machine, Screen


@pytest.mark.parametrize("mode", ["busy", "idle", "http-busy", "http-idle"])
def test_new_full_service_process_reuses_original_native_executor(
    client, tmp_path, mode
):
    scripts = str(Path(__file__).resolve().parents[3] / "scripts/remote_execution")
    sys.path.insert(0, scripts)
    import headless_contract

    machine = Machine(headless_contract, tmp_path)
    project = post_project(
        client, {"name": "Native process recovery"}, owner="alice"
    ).json()["data"]
    project_id, topic = uuid.UUID(project["id"]), uuid.UUID(project["root_topic_id"])
    busy = mode.endswith("busy")
    http = mode.startswith("http-")
    default_seat = room_agent_seat(client, str(topic))
    recipient = default_seat
    if http:
        made = client.post(f"/projects/{project_id}/agents", json={"handle": "opus"})
        assert made.status_code == 200, made.text
        recipient = made.json()["data"]["seat_handle"]
        joined = client.post(
            f"/topics/{topic}/members",
            json={"handle": recipient, "role": "member", "actor": "alice"},
            headers=session_auth_headers("alice"),
        )
        assert joined.status_code == 200, joined.text
        assert recipient != default_seat
        add_external_member(client, str(project_id), "bob", by="alice")
    screen = None
    handle = None
    old = None
    gate = tmp_path / "continue-native"
    target_path = tmp_path / "correction-input.json"
    model_trace = None
    if http and busy:
        from tests.native_http_model import continue_until_echo

        model_trace = continue_until_echo(
            machine, headless_contract, lambda: screen, target_path
        )

    class Channel(SeatChannel):
        name = "native-socket-fixture"
        device = "isolated-device"

        async def open(self, session, agent, launch):
            nonlocal screen, handle
            if screen is None:
                screen = Screen(machine, env={"CHEESE_AUTHOR": agent})
                status = await asyncio.to_thread(screen.call, "ping")
                handle = SimpleNamespace(
                    session=session,
                    agent_handle=agent,
                    session_id=status["session_id"],
                    state=str(machine.state),
                )

        async def call(self, held, method, params):
            return await asyncio.to_thread(screen.call, method, params)

    before = Channel()
    old = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(machine.workspace),
        compute=ComputePool([before.runtime], before.name),
    )
    app.dependency_overrides[get_chat_service] = lambda: old
    try:
        directive = (
            headless_contract.do(
                "Bash",
                command=(
                    f"for i in $(seq 1 1200); do test -f '{gate}' && break; "
                    "sleep 0.05; done; printf PROCESS_RECOVERY"
                ),
                description="wait for backend process recovery",
            )
            if busy
            else "初始回答"
        )
        with client.websocket_connect(chat_ws_url(str(topic), "alice")) as ws:
            # 这个 socket 只把房间里的东西推过来，不收发言（`test_chat_ws_auth`
            # 钉的就是那条拒绝）；说话走 POST，和别的用例一样。
            post_message(
                client, str(topic), "alice", {"content": f"<@{recipient}> " + directive}
            )
            observed = _until(
                ws,
                lambda frame: (
                    frame["type"] == "error"
                    or (
                        (
                            frame["type"] == "event_block"
                            and "PROCESS_RECOVERY" in str(frame["block"])
                        )
                        if busy
                        else frame["type"] == "done"
                    )
                ),
            )
            assert observed["type"] != "error", observed

        async def first_work_id():
            records = await asyncio.to_thread(screen.records)
            return next(
                row["record"]["cheese"]["work_id"]
                for row in records
                if row["record"]["cheese"].get("turn_start")
            )

        question = None
        if http:
            # 这道题是**已有的那一形状**：非组题，`POST /topics/blocks/{id}/answers`
            # 答它（重试、非原答者 422、原答者更正到 v2）。新建的题都是整组的，而组
            # 成员被那条路由整组拒收，走 `settle` —— 那条路另有用例。本例钉的是重启
            # 之后原执行器接着把这道题答完，不是「怎么问出一道题」。
            from tests.ask_fixtures import legacy_question

            question = legacy_question(
                client,
                topic,
                seat=recipient,
                # 题挂在这一轮真正跑过的那条 turn 上，`ask_origin` 的 owner/turn
                # 因此都是真的：`turn` 在 `agent_turns` 里存在，native session 是
                # 这台执行器自己的。
                turn=client.portal.call(first_work_id),
                native_session_id=handle.session_id,
                harness=before.runtime.harness,
                question="恢复后接着执行哪个方案？",
                asked="alice",
                options=[{"text": "继续"}, {"text": "更正"}],
            )
            assert question["author"] == recipient

        async def replace_backend():
            if not busy:
                await settle_turn(old, topic)
            await before.runtime.stop_listening()
            status = await asyncio.to_thread(screen.call, "ping")
            first_work = await first_work_id()
            assert status["working"] == busy
            ids = (
                await _blocks(client.test_request_factory, project_id, topic)
                if busy
                else ()
            )
            async with client.test_request_factory() as session:
                database = session.bind.url.render_as_string(hide_password=False)
            descriptor = {
                "mode": mode,
                "database": database,
                "project": str(project_id),
                "topic": str(topic),
                "agent": handle.agent_handle,
                "session_agent": handle.session.agent_handle,
                "state": handle.state,
                "placed_state": before.seats[(topic, handle.agent_handle)][1],
                "root": str(before.root),
                "native": handle.session_id,
                "workspace": str(machine.workspace),
                "work": first_work,
                "native_pid": status["pid"],
                "gate": str(gate),
                "correction_target": str(target_path),
                "blocks": [str(x) for x in ids],
                "question": question["id"] if question else None,
                "default_seat": default_seat,
                "alice_headers": session_auth_headers("alice") if http else None,
                "bob_headers": session_auth_headers("bob") if http else None,
            }
            descriptor_path = tmp_path / "backend-recovery.json"
            descriptor_path.write_text(json.dumps(descriptor))
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "tests/native_executor_recovery_worker.py",
                str(descriptor_path),
                env={
                    **os.environ,
                    "PYTHONPATH": ".",
                    "DATABASE_URL": database,
                    "CHEESEX_TEST_NULLPOOL": "1",
                },
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            async with asyncio.timeout(120):
                stdout, stderr = await child.communicate()
            assert child.returncode == 0, stderr.decode() + stdout.decode()
            result = json.loads(stdout)
            assert result["pid"] == child.pid and child.pid != os.getpid()
            assert result["native"] == handle.session_id
            assert result["native_pid"] == status["pid"]
            # The worker holds the busy HTTP case to "no turn left running"
            # rather than a row count: the backgrounded gate command can wake
            # the session for a turn of its own after the work ends.
            if not (busy and http):
                assert result["turns"] == (1 if busy else (3 if http else 2))
            if http:
                print(stderr.decode(), flush=True)
                print(json.dumps(result), flush=True)
            final = await asyncio.to_thread(screen.call, "ping")
            assert final["alive"] and final["session_id"] == handle.session_id
            assert final["pid"] == status["pid"] and not final["working"]
            assert screen.process.poll() is None

        client.portal.call(replace_backend)
    finally:
        gate.touch()
        if model_trace is not None and model_trace.exists():
            print("HTTP_MODEL_BOUNDARIES " + model_trace.read_text(), flush=True)
        if old is not None:
            client.portal.call(before.runtime.stop_listening)
        if screen is not None:
            screen.stop()
        machine.close()
        sys.path.remove(scripts)
