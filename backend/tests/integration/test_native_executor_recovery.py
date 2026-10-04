"""Full service replacement retains a pinned native executor and its work.

Claude 2.1.282 uses the local deterministic Messages API. The channel forwards
real runner dispatch, not manufactured echoes/results. Replacement is in the
same Python process; this does not prove a new backend interpreter or upgrade.
"""

import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.harness.claude_code.protocol import INPUT_PROTOCOL
from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.agent.harness.claude_code.runtime import Handle
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks
from app.main import app
from tests.conftest import settle_turn
from tests.integration.conftest import chat_ws_url, post_message, post_project
from tests.integration.test_claude_session_records import _until
from tests.integration.test_native_batch_ownership import _blocks
from tests.unit.test_claude_runner import Machine


def test_native_original_executor_survives_full_service_recovery_and_busy_input(
    client, tmp_path
):
    scripts = str(Path(__file__).resolve().parents[3] / "scripts/remote_execution")
    sys.path.insert(0, scripts)
    import headless_contract

    machine = Machine(headless_contract, tmp_path)
    project = post_project(
        client, {"name": "Native executor recovery"}, owner="alice"
    ).json()["data"]
    project_id, topic = uuid.UUID(project["id"]), uuid.UUID(project["root_topic_id"])
    runner = None
    handle = None
    operations = []

    class Channel:
        name = "native-recovery-fixture"
        deferred_work = False
        builds_model_env = False

        def available(self):
            return True

        async def ensure(self, session, opening, live=None):
            nonlocal runner, handle
            if live is not None:
                return live
            if runner is None:
                runner = Runner(machine.state)
                native = await runner.start(
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
                    frozenset(runner.capabilities),
                )
            return handle

        async def call(self, held, method, params):
            assert held is handle
            operations.append(method)
            return await runner.dispatch(method, params)

        async def discover(self, device_id):
            return [handle] if handle is not None else []

        async def images(self, held, images):
            assert not images
            return []

    def service(channel):
        from app.domain.agent.compute import ComputePool
        from app.domain.agent.harness.claude_code.runtime import ClaudeCodeRuntime

        channel.runtime = ClaudeCodeRuntime(channel)
        return ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(machine.workspace),
            compute=ComputePool([channel.runtime], channel.name),
        )

    before = Channel()
    old = service(before)
    app.dependency_overrides[get_chat_service] = lambda: old
    try:
        gate = tmp_path / "continue-native"
        directive = headless_contract.do(
            "Bash",
            command=(
                f"for i in $(seq 1 1200); do test -f '{gate}' && break; "
                "sleep 0.05; done; printf NATIVE_RECOVERY"
            ),
            description="wait for recovery",
        )
        with client.websocket_connect(chat_ws_url(str(topic), "alice")) as ws:
            # 这个 socket 只推不收（`test_chat_ws_auth` 钉的就是那条拒绝）；
            # 说话走 POST。
            post_message(client, str(topic), "alice", {"content": "@芝士 " + directive})
            _until(
                ws,
                lambda frame: (
                    frame["type"] == "event_block"
                    and "NATIVE_RECOVERY" in str(frame["block"])
                ),
            )
        native, work, process = (
            runner.session_id,
            uuid.UUID(runner.work),
            runner.process,
        )
        sent = operations.count("send")
        client.portal.call(before.runtime.stop_listening)
        after = Channel()
        recovered = service(after)
        app.dependency_overrides[get_chat_service] = lambda: recovered
        assert client.portal.call(recovered.recover_sessions) == 1
        client.portal.call(recovered.replays_settled)
        assert runner.process is process
        assert runner.session_id == native and runner.work == str(work)
        assert operations.count("send") == sent
        assert (topic, work) in recovered._hook_work

        async def continue_and_verify():
            factory = client.test_request_factory
            ids = await _blocks(factory, project_id, topic)
            assert await recovered.notify_running_turn(
                topic, "原答者已提交回答", blocks=ids
            )
            assert runner.working and runner.work == str(work)
            gate.touch()
            async with asyncio.timeout(90):
                while runner.working:
                    await asyncio.sleep(0.05)
            await settle_turn(recovered, topic)
            async with factory() as session:
                rows = list(
                    await session.scalars(
                        select(NativeInput).where(NativeInput.topic_id == topic)
                    )
                )
                assert len(rows) == 2
                assert {row.native_session_id for row in rows} == {native}
                assert {row.execution_work_id for row in rows} == {work}
                assert all(row.echoed_at and row.settled_at for row in rows)
                assert all(
                    set(row.held_block_ids) <= set(row.released_block_ids)
                    for row in rows
                )
                assert (
                    await held_blocks(
                        session,
                        project_id=project_id,
                        topic_id=topic,
                        recipient_handle=rows[0].recipient_handle,
                    )
                    == set()
                )
                all_ids = {
                    uuid.UUID(block) for row in rows for block in row.held_block_ids
                }
                assert all_ids >= set(ids) and len(all_ids) > len(ids)
                blocks = list(
                    await session.scalars(select(Block).where(Block.id.in_(all_ids)))
                )
                assert {consumed_turn(block) for block in blocks} == {str(work)}
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(AgentTurn).where(AgentTurn.topic_id == topic)
                            )
                        )
                    )
                    == 1
                )
            await after.runtime.stop_listening()

        client.portal.call(continue_and_verify)
        assert operations.count("send") == sent
        assert operations.count("steer") == 1
        assert runner.process is process and process.returncode is None
    finally:
        if runner is not None:
            client.portal.call(runner.close)
        machine.close()
        sys.path.remove(scripts)
