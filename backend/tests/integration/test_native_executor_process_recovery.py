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

import pytest

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.claude_code.runtime import ClaudeCodeRuntime, Handle
from app.main import app
from tests.conftest import settle_turn
from tests.integration.conftest import (
    add_external_member,
    chat_ws_url,
    post_project,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_claude_session_records import _until
from tests.integration.test_native_batch_ownership import _blocks
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
        client, {"name": "Native process recovery", "owner_handle": "alice"}
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
    if http and busy:
        import time

        def hold_completion(body):
            # Claude backgrounds a Bash wait when a steer arrives. Hold the
            # deterministic model response, not its stdin/receipt processing.
            deadline = time.monotonic() + 60
            while not gate.exists():
                assert time.monotonic() < deadline, "HTTP correction gate timed out"
                time.sleep(0.05)
            return None

        machine.server.state["actions"] = [
            headless_contract.directive,
            hold_completion,
        ]

    class Channel:
        name = "native-socket-fixture"
        provisions_machine = False
        deferred_work = False
        builds_model_env = False

        def available(self):
            return True

        async def prepare_topic(self, **kwargs):
            return True, ""

        async def ensure(self, session, opening):
            nonlocal screen, handle
            if screen is None:
                screen = Screen(
                    machine,
                    env={"CHEESE_AUTHOR": opening.agent_handle or session.agent_handle},
                )
                status = await asyncio.to_thread(screen.call, "ping")
                handle = Handle(
                    session,
                    "isolated-device",
                    str(machine.state),
                    status["session_id"],
                    opening.agent_handle or session.agent_handle,
                    tmp_path / "mirror.sqlite",
                    status["input_protocol"],
                )
            return handle

        async def call(self, held, method, params):
            assert held is handle
            return await asyncio.to_thread(screen.call, method, params)

        async def images(self, held, images):
            assert not images
            return []

    before = Channel()
    before.runtime = ClaudeCodeRuntime(before)
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
            ws.send_json({"type": "message", "content": f"<@{recipient}> " + directive})
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

        question = None
        if http:
            from app.core.sandbox_auth import mint_scoped_token

            asked = client.post(
                f"/topics/{topic}/ask",
                json={
                    "question": "恢复后接着执行哪个方案？",
                    "options": [{"text": "继续"}, {"text": "更正"}],
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
            assert question["author"] == recipient

        async def replace_backend():
            if not busy:
                await settle_turn(old, topic)
            await before.runtime.stop_listening()
            status = await asyncio.to_thread(screen.call, "ping")
            records = await asyncio.to_thread(screen.records)
            first_work = next(
                row["record"]["cheese"]["work_id"]
                for row in records
                if row["record"]["cheese"].get("turn_start")
            )
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
                "native": handle.session_id,
                "protocol": handle.input_protocol,
                "mirror": str(handle.mirror),
                "workspace": str(machine.workspace),
                "work": first_work,
                "native_pid": status["pid"],
                "gate": str(gate),
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
        if old is not None:
            client.portal.call(before.runtime.stop_listening)
        if screen is not None:
            screen.stop()
        machine.close()
        sys.path.remove(scripts)
