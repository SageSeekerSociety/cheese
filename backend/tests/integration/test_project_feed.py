"""A project's pages are told when something in it changed, instead of asking.

The channel list, the unread marks and a person's levels used to be read again
every 30 seconds by every open page. A page now watches its project on the rooms
socket (`project:<id>`) and hears which of those changed and in which room; it
reads that again itself, so a frame never carries one person's marks to another.

A room only its people see is never named to anyone else, and a change that
concerns one person (they read a room) reaches that person alone.
"""

import time

from starlette.websockets import WebSocketDisconnect

from tests.integration.conftest import (
    join_project_team,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)


def _project(client) -> str:
    """dave's project; alice, bob and carol are on its team."""
    pid = post_project(client, json={"name": "P"}, owner="dave").json()["data"]["id"]
    for handle in ("alice", "bob", "carol"):
        join_project_team(client, pid, handle)
    return pid


def _channel(client, pid: str, *, by: str = "alice", private: bool = False) -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": "频道", "members_only": private},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _private_chat(client, pid: str, user: str, peer: str) -> str:
    r = client.get(
        f"/projects/{pid}/private-chat",
        params={"user_handle": user, "peer_handle": peer},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _heard_until(ws, done) -> list[dict]:
    """Frames heard on the project until ``done(frames)`` holds."""
    frames: list[dict] = []
    while not done(frames):
        frames.append(ws.receive_json())
    return frames


def _about(room: str, *resources: str):
    """Done once every one of these resources was heard about ``room``."""

    def done(frames: list[dict]) -> bool:
        heard = {f.get("resource") for f in frames if f.get("id") == room}
        return set(resources) <= heard

    return done


def test_something_said_in_a_channel_reaches_the_project_s_other_pages(client):
    pid = _project(client)
    room = _channel(client, pid)

    with room_socket(client, f"project:{pid}", "bob") as ws:
        assert ws.subscribed is not None
        post_message(client, room, "alice", {"content": "大家看一下"})
        frames = _heard_until(ws, _about(room, "topics", "unread"))

    # It says what changed and where; nobody's marks ride on it.
    for frame in frames:
        assert set(frame) <= {"type", "resource", "id"}


def test_a_page_outside_the_project_is_refused(client):
    pid = _project(client)

    with room_socket(client, f"project:{pid}", "mallory") as ws:
        assert ws.subscribed is None
        try:
            frame = ws.receive_json()
        except WebSocketDisconnect:
            frame = None
        assert frame is not None and frame.get("code") == "forbidden"


def test_a_private_channel_is_named_only_to_its_people(client):
    pid = _project(client)
    hidden = _channel(client, pid, private=True)  # alice's, bob is not in it
    public = _channel(client, pid)

    with (
        room_socket(client, f"project:{pid}", "bob") as bob,
        room_socket(client, f"project:{pid}", "alice") as alice,
    ):
        post_message(client, hidden, "alice", {"content": "只在这里说"})
        _heard_until(alice, _about(hidden, "topics", "unread"))
        # Something bob is sure to hear, after the hidden room's frames went out.
        time.sleep(0.2)
        post_message(client, public, "alice", {"content": "公开的一句"})
        frames = _heard_until(bob, _about(public, "topics", "unread"))

    assert not [f for f in frames if f.get("id") == hidden]


def test_a_private_chat_tells_only_the_two_people_in_it(client):
    pid = _project(client)
    chat = _private_chat(client, pid, "alice", "bob")
    public = _channel(client, pid)

    with (
        room_socket(client, f"project:{pid}", "bob") as bob,
        room_socket(client, f"project:{pid}", "carol") as carol,
    ):
        post_message(client, chat, "alice", {"content": "私下问一句"})
        heard = _heard_until(bob, _about(chat, "private_unread"))
        time.sleep(0.2)
        post_message(client, public, "alice", {"content": "公开的一句"})
        frames = _heard_until(carol, _about(public, "topics", "unread"))

    # Not in the channel list: nothing says its row changed.
    assert not [f for f in heard if f.get("id") == chat and f["resource"] == "topics"]
    assert not [f for f in frames if f.get("id") == chat]


def test_reading_a_room_tells_the_reader_s_other_pages_and_nobody_else(client):
    pid = _project(client)
    room = _channel(client, pid)
    public = _channel(client, pid, by="carol")
    post_message(client, room, "alice", {"content": "有一件事"})

    with (
        room_socket(client, f"project:{pid}", "bob") as phone,
        room_socket(client, f"project:{pid}", "carol") as carol,
    ):
        r = client.post(
            f"/topics/{room}/read", json={}, headers=session_auth_headers("bob")
        )
        assert r.status_code == 200, r.text
        _heard_until(phone, _about(room, "unread"))
        time.sleep(0.2)
        post_message(client, public, "carol", {"content": "别的事"})
        frames = _heard_until(carol, _about(public, "topics", "unread"))

    assert not [f for f in frames if f.get("id") == room]


def test_a_new_channel_reaches_the_project_s_other_pages(client):
    pid = _project(client)

    with room_socket(client, f"project:{pid}", "bob") as ws:
        room = _channel(client, pid)
        _heard_until(ws, _about(room, "topics"))


def test_whoever_is_taken_out_of_a_private_channel_is_told(client):
    pid = _project(client)
    hidden = _channel(client, pid, private=True)
    r = client.post(
        f"/topics/{hidden}/members",
        json={"handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    with room_socket(client, f"project:{pid}", "bob") as ws:
        r = client.delete(
            f"/topics/{hidden}/members/bob", headers=session_auth_headers("alice")
        )
        assert r.status_code == 200, r.text
        _heard_until(ws, _about(hidden, "topics"))


def test_a_channel_made_private_leaves_everyone_else_s_list(client):
    pid = _project(client)
    room = _channel(client, pid)

    with room_socket(client, f"project:{pid}", "bob") as ws:
        r = client.put(
            f"/topics/{room}/members-only",
            json={"members_only": True},
            headers=session_auth_headers("alice"),
        )
        assert r.status_code == 200, r.text
        _heard_until(ws, _about(room, "topics"))
