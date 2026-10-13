"""A 支线 is a conversation with the teammate who was called into it.

A channel can seat several AI teammates. Once a person has called one of them
into a 支线, what they say there next without calling anyone is still said to
that teammate: it joins the turn running there, or rides into the next turn of
that teammate. Handed to the channel's default teammate instead, it reached
nobody — the default has no conversation there, and the called teammate's next
turn left out a message addressed to somebody else, so calling it again
delivered an empty message.
"""

import time
import uuid

from app.api.deps import get_chat_service
from tests.conftest import settle_turn, wait_work_idle
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)


def _room_with_two_teammates(client) -> tuple[str, str]:
    """A channel seating the project's default teammate and one more; returns
    the channel and the second teammate's seat."""
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    default = room_agent_seat(client, room)
    made = client.post(f"/projects/{p['id']}/agents", json={"handle": "opus"})
    assert made.status_code == 200, made.text
    teammate = made.json()["data"]["seat_handle"]
    joined = client.post(
        f"/topics/{room}/members",
        json={"handle": teammate, "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert joined.status_code == 200, joined.text
    assert teammate != default
    return room, teammate


def _say(client, conversation: str, text: str) -> None:
    """alice says one thing there and waits for whatever it started to end."""
    with room_socket(client, conversation, "alice") as ws:
        post_message(client, conversation, "alice", {"content": text})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(conversation))
    wait_work_idle()


def _wait_for_prompt(stub_hooks, needle: str) -> None:
    for _ in range(500):
        if needle in (stub_hooks.last_prompt or ""):
            return
        time.sleep(0.01)
    raise AssertionError(f"the teammate never got it: {stub_hooks.last_prompt!r}")


def test_a_follow_up_in_a_thread_reaches_the_teammate_called_there(client, stub_hooks):
    room, teammate = _room_with_two_teammates(client)
    thread = in_thread(client, room, "alice")
    _say(client, thread, f"<@{teammate}> 这段说明读一下")

    _say(client, thread, "再查一下出处")
    _say(client, thread, f"<@{teammate}>")

    _wait_for_prompt(stub_hooks, "再查一下出处")


def test_a_follow_up_reaches_the_teammate_called_in_the_main_line(client, stub_hooks):
    """Called from the channel's main line, the teammate answers in the 支线
    under that message: the 支线 is a conversation with it from the start."""
    room, teammate = _room_with_two_teammates(client)
    with room_socket(client, room, "alice"):
        called = post_message(
            client, room, "alice", {"content": f"<@{teammate}> 这段说明读一下"}
        )
    _wait_for_prompt(stub_hooks, "这段说明读一下")
    opened = client.post(
        f"/blocks/{called['id']}/thread", headers=session_auth_headers("alice")
    )
    assert opened.status_code == 200, opened.text
    thread = opened.json()["data"]["id"]
    service = client.app.dependency_overrides[get_chat_service]()
    client.portal.call(settle_turn, service, uuid.UUID(thread))
    wait_work_idle()

    _say(client, thread, "再查一下出处")
    _say(client, thread, f"<@{teammate}>")

    _wait_for_prompt(stub_hooks, "再查一下出处")
