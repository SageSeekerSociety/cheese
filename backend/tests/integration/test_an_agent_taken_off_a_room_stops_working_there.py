"""An agent taken off a room stops working in it.

Off the roster, every call its credential makes in the room is refused, so a
turn it still has running there can only fail at whatever it tries next. The
turn is stopped when the agent is taken off; what it wrote so far stays.
"""

import time

from tests.integration.conftest import (
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
    post_message(client, room, "alice", {"content": "@芝士 跑一下测试"})
    assert _until(lambda: _working(client, room) == [seat])

    removed = client.delete(
        f"/topics/{room}/members/{seat}", headers=session_auth_headers("alice")
    )
    assert removed.status_code == 200, removed.text

    assert _until(lambda: _working(client, room) == [])
