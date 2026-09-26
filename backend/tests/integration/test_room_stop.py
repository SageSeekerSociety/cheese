"""A person in the room can stop a run, and a stopped run stays stopped.

The case this exists for: a room's session stops answering mid-run. Everything
said in the room meanwhile queues behind the run ("消息未能送达进行中的会话"),
including 「停停」, and the batch is sent again into every later run. The
stop is the room's control, and it has to work exactly then — when the session
is the thing not listening.

These drive real turns through the room's socket and the stop through the
route the room's button calls, and assert what a person in the room sees.
"""

import time
import uuid

import pytest

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.main import app
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import (
    chat_ws_url,
    post_project,
    session_auth_headers,
)


class StuckOnceScreen(StubChannel):
    """The first prompt starts a command that never returns; later prompts are
    answered normally."""

    def __init__(self, **policy: float) -> None:
        super().__init__(**policy)
        self.stuck = False

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        if self.stuck:
            super().emit_turn(topic_id, prompt, reply)
            return
        self.stuck = True
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.uses(topic_id, "Bash", command="sleep 600")


def _room(client) -> tuple[str, StuckOnceScreen, ChatService]:
    project_id = post_project(client, json={"name": "Stop"}).json()["data"]["id"]
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "停下", "created_by": "user-1"},
    ).json()["data"]["id"]
    screen = StuckOnceScreen()
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root="/tmp/stop-ws",
        compute=stub_compute(screen),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return topic_id, screen, service


def _start_stuck_run(client, topic_id: str, text: str) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": f"{text} @芝士"})
        while True:
            frame = ws.receive_json()
            if frame["type"] == "event_block" and "sleep 600" in str(frame["block"]):
                return


def _say(client, topic_id: str, text: str) -> None:
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json({"type": "message", "content": f"{text} @芝士"})
        while ws.receive_json()["type"] != "done":
            pass


def _stop(client, topic_id: str) -> dict:
    headers = session_auth_headers("user-1")
    state = client.get(f"/topics/{topic_id}/agent/control", headers=headers)
    assert state.status_code == 200, state.text
    session_id = state.json()["data"]["id"]
    assert session_id, "a room with a run open has a session to stop"
    result = client.post(
        f"/topics/{topic_id}/agent/control",
        headers=headers,
        json={"session_id": session_id, "request": {"subtype": "interrupt"}},
    )
    assert result.status_code == 200, result.text
    return result.json()["data"]["result"]["response"]


def _ended(service: ChatService, room: uuid.UUID) -> None:
    """The room's books have the run closed — without reading the runner, which
    may be the thing out of reach."""
    deadline = time.monotonic() + 5
    while any(topic == room for topic, _ in service._hook_work):
        assert time.monotonic() < deadline, "the stopped run never closed"
        time.sleep(0.01)


def _platform_lines(client, topic_id: str) -> list[str]:
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    return [b["content"] for b in blocks if b["author_type"] == "platform"]


@pytest.mark.parametrize("session_answers", [True, False])
def test_a_stopped_run_ends_at_once_and_its_messages_are_not_sent_again(
    client, session_answers
):
    topic_id, screen, service = _room(client)
    room = uuid.UUID(topic_id)
    _start_stuck_run(client, topic_id, "第一句")
    if not session_answers:
        # The runner is out of reach: every call to it now fails, the way a
        # room's session looks while its machine is gone.
        runner = screen.sessions.pop(room)

    response = _stop(client, topic_id)
    assert response["subtype"] == "success"
    assert response["response"] == {"stopped": True}

    # Ended now, not after the platform has given up waiting on the runner.
    _ended(service, room)
    lines = _platform_lines(client, topic_id)
    assert "<@user-1> 停止了这次运行" in lines, lines
    # Nothing broke, and the room is not told something did.
    assert not [line for line in lines if "没跑完" in line or "exited" in line], lines

    if not session_answers:
        # Whatever the session prints for the stopped run when it comes back
        # is not a second ending.
        screen.sessions[room] = runner
        screen.returns(room, "Bash", "late")
        screen.stops(room, "late")
        client.portal.call(screen.runtime.subscriptions[room].drain)
        lines = _platform_lines(client, topic_id)
        assert not [line for line in lines if "没跑完" in line], lines
        assert "late" not in [
            b["content"]
            for b in client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
        ]

    # The next message starts a run of its own; the stopped one's message is
    # not carried into it.
    _say(client, topic_id, "第二句")
    assert screen.last_prompt is not None
    assert "第二句" in screen.last_prompt
    assert "第一句" not in screen.last_prompt, screen.last_prompt


def test_a_stop_with_no_run_open_says_nothing_was_stopped(client):
    topic_id, screen, service = _room(client)
    screen.stuck = True  # answer the first prompt normally
    _say(client, topic_id, "你好")
    client.portal.call(settle_turn, service, uuid.UUID(topic_id))

    response = _stop(client, topic_id)
    assert response == {
        "subtype": "success",
        "response": {"stopped": False},
        "request_id": response["request_id"],
    }
    assert "<@user-1> 停止了这次运行" not in _platform_lines(client, topic_id)
