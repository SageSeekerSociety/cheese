"""A message stored between reading a room and subscribing to it is not lost.

A page reads a room's history over HTTP and hears what lands after that on the
rooms socket. Those are two paths, and a message stored after the read but
before the subscription is registered travels by neither: the read was too
early and the socket too late. In a quiet room nothing comes after it to give
it away, so it stays missing until the page is reloaded.

So the acknowledgement names the room's newest message as of the moment the
subscription took hold. A page that does not hold that message reads the tail
again; anything stored later is published to it.
"""

from tests.integration.conftest import (
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)


def _room(client) -> str:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    return client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]


def _newest_read(client, room: str) -> str | None:
    r = client.get(
        f"/topics/{room}/blocks?limit=50", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["newest_id"]


def test_a_message_stored_after_the_read_is_named_when_the_room_is_subscribed(
    client,
):
    room = _room(client)
    read = _newest_read(client, room)
    between = post_message(client, room, "alice", {"content": "读完历史之后才说的"})
    assert between["id"] != read

    with room_socket(client, room, "alice") as ws:
        assert ws.subscribed["newest"] == between["id"]


def test_the_newest_message_matches_what_a_fresh_read_ends_with(client):
    room = _room(client)
    post_message(client, room, "alice", {"content": "第一句"})

    with room_socket(client, room, "alice") as ws:
        assert ws.subscribed["newest"] == _newest_read(client, room)
