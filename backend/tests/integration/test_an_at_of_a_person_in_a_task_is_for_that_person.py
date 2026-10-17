"""In a task, a message that @-s a person and no AI teammate is for that person.

A task calls its teammate with every message its people write there, without
an @. The exception is a message that says whom it is for: one that @-s a
person, and no AI teammate, is said to that person, and the task's teammate
does not pick it up and answer it.

On dev (2026-10-14) a task's owner twice wrote a message there that @-ed only
a colleague; both times the task's teammate took it as its own and answered.
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
from tests.conftest import StubChannel, settle_turn, stub_compute, wait_work_idle
from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_message,
    post_project,
)


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _inputs(client, task: uuid.UUID) -> list[NativeInput]:
    """What the task's teammate has been handed, one row per input."""

    async def read():
        async with client.test_factory() as session:
            return list(
                await session.scalars(
                    select(NativeInput).where(NativeInput.conversation_id == task)
                )
            )

    return asyncio.run(read())


def _task_after_one_turn(client, tmp_path) -> tuple[StubChannel, str]:
    """A task whose teammate has answered alice once and is idle again."""
    data = post_project(client, {"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, data["id"], "bob")
    screen = StubChannel()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=stub_compute(screen),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    task = open_task(client, data["root_topic_id"], "整理反馈", owner="alice")
    task_id = uuid.UUID(task["id"])
    post_message(client, task["id"], "alice", {"content": "先看一下"})
    _until(lambda: _inputs(client, task_id), "the task's first turn never ran")
    _until(
        lambda: all(row.completed_at for row in _inputs(client, task_id)),
        "the task's first turn never finished",
    )
    return screen, task["id"]


def _settle(client, task: str) -> None:
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(task))
    wait_work_idle()


def test_a_message_at_a_person_in_a_task_does_not_call_its_teammate(client, tmp_path):
    screen, task = _task_after_one_turn(client, tmp_path)
    handed = len(_inputs(client, uuid.UUID(task)))

    post_message(client, task, "alice", {"content": "<@bob> 你看这个反馈"})
    _settle(client, task)

    assert len(_inputs(client, uuid.UUID(task))) == handed
    assert "你看这个反馈" not in (screen.last_prompt or "")


def test_a_message_without_an_at_in_a_task_still_calls_its_teammate(client, tmp_path):
    screen, task = _task_after_one_turn(client, tmp_path)

    post_message(client, task, "alice", {"content": "接着做下一步"})

    _until(
        lambda: "接着做下一步" in (screen.last_prompt or ""),
        "the task's teammate never got the message",
    )
