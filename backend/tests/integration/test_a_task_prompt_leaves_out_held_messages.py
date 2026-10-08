"""A task's prompt leaves out what an earlier input of the task still holds.

Holds are registered per conversation, and a task is a conversation of its
own. The prompt used to look them up under the task's room instead, so it
carried a message another input of the task still held; registering the new
input then failed ("Input batch is already held by another native input"),
the turn sent nothing, and its delivery was retried forever.
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api import deps as session_turn_deps
from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.delivery.input_identity import InputEffects, InputIdentity
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import (
    register_input,
    terminate_inputs_of_dead_works,
)
from app.main import app
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import open_task, post_message, post_project

HELD = "这条还挂在一个结果不明的输入上"


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _inputs(client, task: uuid.UUID) -> list[NativeInput]:
    async def read():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(NativeInput).where(NativeInput.conversation_id == task)
                )
            )

    return asyncio.run(read())


def _hold(client, project: uuid.UUID, task: uuid.UUID, seat: str) -> None:
    """A message in the task held by an input whose outcome nobody will learn:
    its turn died, so it was ended without saying whether it was read."""

    async def run():
        async with client.test_factory() as session:
            block = Block(
                id=uuid.uuid4(),
                project_id=project,
                conversation_id=task,
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="alice",
                content=HELD,
                meta={"consumed_turn": None},
            )
            session.add(block)
            await session.commit()
            work = uuid.uuid4()
            identity = InputIdentity(
                project, task, seat, "claude_code", "sess-gone", work, work
            )
            await register_input(
                session, identity, InputEffects(held_block_ids=(block.id,))
            )
            await terminate_inputs_of_dead_works(session, [work], reason="orphaned")
            await session.commit()

    asyncio.run(run())


def test_a_task_turn_sends_without_a_message_held_elsewhere(client):
    data = post_project(client, {"name": "Held"}, owner="alice").json()["data"]
    project, room = uuid.UUID(data["id"]), data["root_topic_id"]
    channel = StubChannel()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/task-held-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    task = open_task(client, room, "整理一下", owner="alice")
    task_id = uuid.UUID(task["id"])
    post_message(client, task["id"], "alice", {"content": "先看一下"})
    _until(lambda: _inputs(client, task_id), "the task's first turn never ran")
    seat = _inputs(client, task_id)[0].recipient_handle
    _until(
        lambda: all(row.completed_at for row in _inputs(client, task_id)),
        "the task's first turn never finished",
    )

    _hold(client, project, task_id, seat)
    post_message(client, task["id"], "alice", {"content": "接着做下一步"})

    _until(
        lambda: "接着做下一步" in (channel.last_prompt or ""),
        "the task's next turn never reached its session",
    )
    assert HELD not in (channel.last_prompt or "")
