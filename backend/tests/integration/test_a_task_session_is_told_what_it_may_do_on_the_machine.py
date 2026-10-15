"""A 支线's session is told when the 支线 becomes a task.

In a 支线 nothing the session does on the machine is kept; once the 支线 is a
task, its work reaches the project and the people whose word it acts on are
the task's owner and collaborators. The session carries the same conversation
on, so nothing it opened with says so: it is told on its next turn, once, and
not on every turn after.
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.api import deps as session_turn_deps
from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.delivery.models import NativeInput
from app.main import app
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import (
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)

THE_TASK = "## 这个任务"
BECAME = "这条支线已经转为任务"


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


def test_a_thread_session_hears_once_that_its_thread_became_a_task(client):
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
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/task-machine-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    seat = room_agent_seat(client, room)
    root = post_message(client, room, "alice", {"content": "登录要加验证码"})
    thread = client.post(
        f"/blocks/{root['id']}/thread", headers=session_auth_headers("alice")
    ).json()["data"]["id"]
    thread_id = uuid.UUID(thread)

    post_message(client, thread, "alice", {"content": f"<@{seat}> 先看看现状"})
    _until(lambda: any("先看看现状" in p for p in heard), "the 支线 never had a turn")
    assert not any(THE_TASK in prompt for prompt in heard)
    _until(
        lambda: all(row.completed_at for row in _inputs(client, thread_id)),
        "the 支线's turn never finished",
    )

    made = client.post(
        f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert made.status_code == 200, made.text
    before = len(heard)
    post_message(client, thread, "alice", {"content": "动手吧"})
    _until(
        lambda: any("动手吧" in prompt for prompt in heard[before:]),
        "the task's next turn never reached its session",
    )
    told = heard[before:]
    assert sum(THE_TASK in prompt for prompt in told) == 1
    assert any(BECAME in prompt for prompt in told)

    _until(
        lambda: all(row.completed_at for row in _inputs(client, thread_id)),
        "the task's turn never finished",
    )
    again = len(heard)
    post_message(client, thread, "alice", {"content": "再改一下按钮"})
    _until(
        lambda: any("再改一下按钮" in prompt for prompt in heard[again:]),
        "the task's later turn never reached its session",
    )
    assert not any(THE_TASK in prompt for prompt in heard[again:])
