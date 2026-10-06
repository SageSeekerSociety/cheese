"""Member activity: who is busy in a room right now, attributed to that member.

A room has no status. A person composing in its input is typing there; an agent
with a turn running there is working there. Both reach the people in that room
over its socket (an ``activity`` frame, and an ``activity_snapshot`` on
connect), and the sidebar reads the same entries off ``GET /topics``. Neither
leaks into another room, even one the same member sits in.
"""

import uuid

from tests.integration.conftest import (
    chat_ws_url,
    in_thread,
    join_project_team,
    new_project,
    post_message,
    room_agent_seat,
    session_auth_headers,
)


def _until(ws, predicate) -> list[dict]:
    frames = []
    while True:
        frames.append(ws.receive_json())
        if predicate(frames[-1]):
            return frames


def _activity(frame: dict) -> bool:
    return frame["type"] == "activity"


def _snapshot(client, room: str, handle: str) -> list[dict]:
    """Who a socket opened now is told is busy. Nothing is sent when nobody is,
    so the ping's answer marks the end of what the connect brought."""
    with client.websocket_connect(chat_ws_url(room, handle)) as ws:
        ws.send_json({"type": "ping"})
        frames = _until(ws, lambda f: f["type"] == "pong")
    snapshots = [f for f in frames if f["type"] == "activity_snapshot"]
    return snapshots[-1]["members"] if snapshots else []


def _who(entries: list[dict]) -> list[tuple[str, str]]:
    return [(e["member"], e["kind"]) for e in entries]


def _two_rooms(client) -> tuple[str, str, str]:
    """A project alice owns and bob is on, with two rooms in it."""
    project = new_project(client, owner="alice")
    join_project_team(client, project["id"], "bob")
    other = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "隔壁"},
        headers=session_auth_headers("alice"),
    )
    assert other.status_code == 200, other.text
    return project["id"], project["root_topic_id"], other.json()["data"]["id"]


def _listed(client, project_id: str, room: str) -> list[dict]:
    rows = client.get(
        f"/topics?project_id={project_id}", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return next(t for t in rows if t["id"] == room)["activity"]


def _header(client, room: str) -> list[dict]:
    got = client.get(f"/topics/{room}", headers=session_auth_headers("alice"))
    return got.json()["data"]["activity"]


def test_a_person_typing_reaches_the_other_members_of_that_room_only(client):
    _, room, other = _two_rooms(client)
    with (
        client.websocket_connect(chat_ws_url(room, "bob")) as bob,
        client.websocket_connect(chat_ws_url(room, "alice")) as alice,
    ):
        alice.send_json({"type": "typing"})
        seen = _until(bob, _activity)[-1]
        assert seen["member"] == "alice"
        assert seen["kind"] == "typing"
        assert seen["active"] is True
        assert seen["expires_in"] > 0

        # Someone who opens the room now is told who is typing in it; someone
        # who opens the other room is not.
        joined = _snapshot(client, room, "bob")
        assert [(m["member"], m["kind"]) for m in joined] == [("alice", "typing")]
        assert _snapshot(client, other, "bob") == []

        alice.send_json({"type": "typing", "active": False})
        stopped = _until(bob, _activity)[-1]
        assert (stopped["member"], stopped["active"]) == ("alice", False)


def test_a_message_landing_ends_its_authors_typing(client):
    _, room, _ = _two_rooms(client)
    with (
        client.websocket_connect(chat_ws_url(room, "bob")) as bob,
        client.websocket_connect(chat_ws_url(room, "alice")) as alice,
    ):
        alice.send_json({"type": "typing"})
        _until(bob, _activity)
        post_message(client, room, "alice", {"content": "周五交初稿"})
        frames = _until(bob, lambda f: f["type"] == "user_block")

    ended = [f for f in frames if _activity(f)]
    assert [(f["member"], f["kind"], f["active"]) for f in ended] == [
        ("alice", "typing", False)
    ]
    assert _snapshot(client, room, "bob") == []


def test_the_member_is_the_sockets_owner_not_a_field_of_the_frame(client):
    _, room, _ = _two_rooms(client)
    with (
        client.websocket_connect(chat_ws_url(room, "bob")) as bob,
        client.websocket_connect(chat_ws_url(room, "alice")) as alice,
    ):
        alice.send_json({"type": "typing", "member": "bob"})
        seen = _until(bob, _activity)[-1]
    assert seen["member"] == "alice"


def test_an_agent_turn_is_that_agent_working_in_that_room_only(client, stub_hooks):
    project_id, room, other = _two_rooms(client)

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="sleep 60")

    stub_hooks.emit_turn = turn
    seat = room_agent_seat(client, room)
    # 芝士 is called in a 支线 of the room and works there.
    thread = in_thread(client, room, "alice")
    with client.websocket_connect(chat_ws_url(thread, "alice")) as ws:
        post_message(client, thread, "alice", {"content": "@芝士 跑一下测试"})
        started = _until(ws, lambda f: _activity(f) and f["kind"] == "working")[-1]
        assert started["member"] == seat
        assert started["active"] is True

        working = [(seat, "working")]
        assert _who(_snapshot(client, thread, "bob")) == working
        # Another room of the same project hears nothing of it.
        assert _snapshot(client, other, "bob") == []
        assert _listed(client, project_id, other) == []

        stub_hooks.stops(uuid.UUID(thread), "好了")
        ended = _until(ws, lambda f: _activity(f) and f["kind"] == "working")[-1]
    assert (ended["member"], ended["active"]) == (seat, False)
    assert _snapshot(client, thread, "bob") == []
