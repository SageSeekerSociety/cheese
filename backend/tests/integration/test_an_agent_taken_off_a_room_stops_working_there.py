"""An agent taken off a room stops working in it.

Off the roster, every call its credential makes in the room is refused, so a
turn it still has running there can only fail at whatever it tries next. The
turn is stopped when the agent is taken off; what it wrote so far stays.
"""

import time

from tests.integration.conftest import (
    chat_ws_url,
    new_project,
    post_message,
    room_agent_seat,
    session_auth_headers,
)


def _working(client, room: str) -> list[str]:
    got = client.get(f"/topics/{room}", headers=session_auth_headers("alice"))
    return [
        entry["member"]
        for entry in got.json()["data"]["activity"]
        if entry["kind"] == "working"
    ]


def _working_in(client, conversation: str) -> list[str]:
    """Who a socket opened on ``conversation`` now is told is working."""
    with client.websocket_connect(chat_ws_url(conversation, "alice")) as ws:
        ws.send_json({"type": "ping"})
        snapshots = []
        while True:
            frame = ws.receive_json()
            if frame["type"] == "activity_snapshot":
                snapshots.append(frame)
            if frame["type"] == "pong":
                break
    members = snapshots[-1]["members"] if snapshots else []
    return [entry["member"] for entry in members if entry["kind"] == "working"]


def _thread_of(client, block_id: str) -> str:
    """The 支线 a call to 芝士 in the main line is answered in."""
    opened = client.post(
        f"/blocks/{block_id}/thread", headers=session_auth_headers("alice")
    )
    assert opened.status_code == 200, opened.text
    return opened.json()["data"]["id"]


def _until(check, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_taking_an_agent_off_a_room_stops_its_running_turn(client, stub_hooks):
    room = new_project(client, owner="alice")["root_topic_id"]

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="sleep 600")

    stub_hooks.emit_turn = turn
    seat = room_agent_seat(client, room)
    asked = post_message(client, room, "alice", {"content": "@芝士 跑一下测试"})
    # It works in the message's 支线.
    thread = _thread_of(client, asked["id"])
    assert _until(lambda: _working_in(client, thread) == [seat])

    removed = client.delete(
        f"/topics/{room}/members/{seat}", headers=session_auth_headers("alice")
    )
    assert removed.status_code == 200, removed.text

    assert _until(lambda: _working_in(client, thread) == [])
