"""Full ChatService recovery on a retained scripted native session.

The channel substitutes native stdout, not receipt/transaction/runtime logic.
This is same-process service replacement, not a native/new-interpreter proof.
"""

import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks
from app.main import app
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    post_message,
    post_project,
)
from tests.integration.test_claude_session_records import StillWorking, _until
from tests.integration.test_native_batch_ownership import _blocks


def test_recovered_original_executor_takes_busy_input_and_releases_both_batches(client):
    project = post_project(
        client, {"name": "Ask executor recovery"}, owner="alice"
    ).json()["data"]
    project_id = uuid.UUID(project["id"])
    # 芝士 answers in a 支线 of the channel.
    topic = uuid.UUID(in_thread(client, project["root_topic_id"], "alice"))

    def service(channel):
        return ChatService(
            session_factory=client.test_request_factory,
            base_system_prompt="你是芝士。",
            workspace_root=str(channel.root / "workspace"),
            compute=stub_compute(channel),
        )

    before = StillWorking()
    old = service(before)
    app.dependency_overrides[get_chat_service] = lambda: old
    with client.websocket_connect(chat_ws_url(str(topic), "alice")) as ws:
        # 这个 socket 只推不收（`test_chat_ws_auth` 钉的就是那条拒绝）；
        # 说话走 POST。
        post_message(client, str(topic), "alice", {"content": "@芝士 等待回答"})
        _until(
            ws,
            lambda frame: (
                frame["type"] == "event_block" and "sleep 600" in str(frame["block"])
            ),
        )
    runner = before._session_for(topic)
    native = runner.session_id
    work = uuid.UUID(runner.work)
    initial_writes = len(runner.written)
    client.portal.call(before.runtime.stop_listening)

    after = StubChannel()
    after.root, after.sessions, after.calls = before.root, before.sessions, before.calls
    for held in after.sessions.values():
        held.channel = after
    recovered = service(after)
    app.dependency_overrides[get_chat_service] = lambda: recovered
    assert client.portal.call(recovered.recover_sessions) == 1
    client.portal.call(recovered.replays_settled)
    assert after._session_for(topic) is runner
    assert runner.session_id == native
    assert len(runner.written) == initial_writes
    assert (topic, work) in recovered.live.hook_work

    async def continue_and_verify():
        factory = client.test_request_factory
        ids = await _blocks(factory, project_id, topic)
        assert await recovered.notify_running_turn(
            topic, "原答者提交了回答", blocks=ids
        )
        await settle_turn(recovered, topic)
        async with factory() as session:
            rows = list(
                await session.scalars(
                    select(NativeInput).where(NativeInput.conversation_id == topic)
                )
            )
            assert len(rows) == 2
            assert {row.native_session_id for row in rows} == {native}
            assert {row.execution_work_id for row in rows} == {work}
            assert all(row.echoed_at and row.settled_at for row in rows)
            assert all(
                set(row.held_block_ids) <= set(row.released_block_ids) for row in rows
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
            blocks = list(await session.scalars(select(Block).where(Block.id.in_(ids))))
            assert {consumed_turn(block) for block in blocks} == {str(work)}
            turns = list(
                await session.scalars(
                    select(AgentTurn).where(AgentTurn.conversation_id == topic)
                )
            )
            assert len(turns) == 1
        await after.runtime.stop_listening()

    client.portal.call(continue_and_verify)
    assert after._session_for(topic) is runner
    assert runner.session_id == native
    assert len(runner.written) == initial_writes + 1
