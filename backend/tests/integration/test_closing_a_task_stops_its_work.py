"""Closing a task stops the work its AI teammate is still doing in it.

A closed task is work that is over. Left running, the teammate went on for
minutes after its owner closed the task: the room kept showing it at work, the
model kept being paid for, and every write it tried was refused because the
task was closed. When the task's own session is the one closing it, that turn
is the one asking, and it ends by itself.
"""

import time
import uuid

from app.api import deps as session_turn_deps
from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.main import app
from tests.conftest import StubChannel, retire_topic, stub_compute
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)


class KeepsWorking(StubChannel):
    """A session that takes every input and never finishes the turn."""

    def emit_turn(self, topic_id, prompt, reply, *, agent=None):
        del reply
        self.starts(topic_id, agent=agent)
        self.acknowledges(topic_id, prompt, agent=agent)


def _until(predicate, what: str) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _interrupts(channel: StubChannel, task: uuid.UUID) -> list[dict]:
    session = channel.sessions and channel._session_for(task)
    return [
        message
        for message in (session.written if session else [])
        if message.get("type") == "control_request"
        and message["request"].get("subtype") == "interrupt"
    ]


def _working_task(client) -> tuple[KeepsWorking, dict, str]:
    """A started task whose teammate is in the middle of its first turn."""
    data = post_project(client, {"name": "Close"}, owner="alice").json()["data"]
    room = data["root_topic_id"]
    channel = KeepsWorking()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/close-task-ws",
        compute=stub_compute(channel),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    task = open_task(client, room, "验证一下", owner="alice")
    task_id = uuid.UUID(task["id"])
    _until(
        lambda: any(key[0] == task_id for key in channel.sessions),
        "starting the task never reached its teammate",
    )
    _until(
        lambda: any(
            m.get("type") == "user" for m in channel._session_for(task_id).written
        ),
        "the teammate never took the task's first input",
    )
    return channel, task, room


def test_the_owner_closing_a_task_stops_the_turn_running_in_it(client):
    channel, task, _ = _working_task(client)
    task_id = uuid.UUID(task["id"])
    try:
        assert _interrupts(channel, task_id) == []

        closed = client.post(
            f"/topics/{task['id']}/close",
            json={"conclusion": "不用做了"},
            headers=session_auth_headers("alice"),
        )
        assert closed.status_code == 200, closed.text

        _until(
            lambda: _interrupts(channel, task_id),
            "closing the task left its teammate working",
        )
    finally:
        retire_topic(client, task_id)
        app.dependency_overrides.pop(get_chat_service, None)


def test_a_task_closed_by_its_own_session_is_not_interrupted(client):
    channel, task, room = _working_task(client)
    task_id = uuid.UUID(task["id"])
    try:
        closed = client.post(
            f"/topics/{task['id']}/close",
            json={"conclusion": "做完了"},
            headers=task_agent_headers(
                task["project_id"], task_id, room_agent_seat(client, room)
            ),
        )
        assert closed.status_code == 200, closed.text
        assert _interrupts(channel, task_id) == []
    finally:
        retire_topic(client, task_id)
        app.dependency_overrides.pop(get_chat_service, None)
