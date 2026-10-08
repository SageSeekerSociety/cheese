"""A 支线's teammate hears of a task made from the 支线's message while its
session is open.

On dev a teammate answered in a 支线, then a person turned the message into a
task, then asked the teammate in the same 支线 to do the work: it made a second
task under the same message. The channel's open tasks were said once, when the
session opened, and never again. Only the session transport is stubbed: the
routes, the turn and what its session is sent are the real ones.
"""

from tests.integration.conftest import (
    post_message,
    post_project,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)


def _ask(client, thread: str, text: str) -> None:
    with room_socket(client, thread, "alice") as ws:
        post_message(client, thread, "alice", {"content": text})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass


def test_a_task_made_from_the_message_reaches_the_open_session(client, stub_hooks):
    alice = session_auth_headers("alice")
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "干活"}, headers=alice
    ).json()["data"]["id"]
    seat = room_agent_seat(client, room)
    root = post_message(client, room, "alice", {"content": "登录页要改"})
    opened = client.post(f"/blocks/{root['id']}/thread", headers=alice)
    assert opened.status_code == 200, opened.text
    thread = opened.json()["data"]["id"]

    _ask(client, thread, f"<@{seat}> 先看一眼")
    assert "从这条支线的消息建的" not in stub_hooks.told

    made = client.post(f"/blocks/{root['id']}/upgrade", json={}, headers=alice)
    assert made.status_code == 200, made.text

    _ask(client, thread, f"<@{seat}> 动手改吧")
    assert "从这条支线的消息建的" in stub_hooks.last_prompt
