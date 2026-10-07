"""A task's session is told what it may do on the work machine.

Until its owner starts the task, nothing the session does on the machine is
kept: its sync and its pushes are refused. Told nothing, it found out by being
refused, read the refusal as a broken sandbox and booked a retry. Starting the
task relaunches the session on the same conversation, so it is told again then,
and only then does its work reach the project.
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.delivery.models import NativeInput
from app.main import app
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import (
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)

NOT_STARTED = "这条任务还没开始"
STARTED = "这条任务已经开始"


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


def test_a_task_session_hears_its_work_is_not_kept_until_the_task_starts(client):
    data = post_project(client, {"name": "Reads"}, owner="alice").json()["data"]
    room = data["root_topic_id"]
    channel = StubChannel()
    heard: list[str] = []
    arrive = channel.arrive

    def recording(topic_id, message, **kwargs):
        arrive(topic_id, message, **kwargs)
        heard.append(channel.last_prompt or "")

    channel.arrive = recording
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/task-machine-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    task = open_task(client, room, "先看看", owner="alice", start=False)
    task_id = uuid.UUID(task["id"])

    post_message(client, task["id"], "alice", {"content": "这里是怎么回事"})
    _until(
        lambda: "这里是怎么回事" in (channel.last_prompt or ""),
        "the task's first turn never reached its session",
    )
    assert NOT_STARTED in channel.last_prompt
    assert STARTED not in channel.last_prompt
    _until(
        lambda: all(row.completed_at for row in _inputs(client, task_id)),
        "the task's first turn never finished",
    )

    started = client.post(
        f"/topics/{task['id']}/start",
        json={"reviewer_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert started.status_code == 200, started.text
    before = len(heard)
    post_message(client, task["id"], "alice", {"content": "动手吧"})
    _until(
        lambda: any("动手吧" in prompt for prompt in heard[before:]),
        "the task's next turn never reached its session",
    )

    # Told once, on the first input after the start (the start itself may
    # bring one), and the read-only line never again.
    after = heard[before:]
    assert sum(STARTED in prompt for prompt in after) == 1
    assert not any(NOT_STARTED in prompt for prompt in after)
