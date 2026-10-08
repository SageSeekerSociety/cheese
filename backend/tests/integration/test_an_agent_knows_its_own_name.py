"""An AI teammate is told its own name.

A project's teammate can be renamed (Nova on dev), and a room can seat several.
The roster lists every one of them, so without being told which name is its own
the teammate called itself by the product's name and wrote 「@芝士：…」 into a
task document. Only the session transport is stubbed: the routes, the turn and
the prompt it is started with are the real ones.
"""

from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)


def test_a_renamed_teammate_is_told_its_name(client, stub_hooks):
    alice = session_auth_headers("alice")
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "干活"}, headers=alice
    ).json()["data"]["id"]
    seat = room_agent_seat(client, room)
    agent = next(
        a
        for a in client.get(f"/projects/{project['id']}/agents", headers=alice).json()[
            "data"
        ]["data"]
        if a["seat_handle"] == seat
    )
    renamed = client.put(
        f"/projects/{project['id']}/agents/{agent['id']}",
        json={"display_name": "Nova"},
        headers=alice,
    )
    assert renamed.status_code == 200, renamed.text

    thread = in_thread(client, room, "alice")
    with room_socket(client, thread, "alice") as ws:
        post_message(client, thread, "alice", {"content": f"<@{seat}> 你好"})
        while ws.receive_json()["type"] not in ("done", "error"):
            pass

    prompt = stub_hooks.last_system_prompt
    assert prompt is not None
    # First, ahead of everything the deployment says about the platform.
    assert prompt.startswith("你的名字是「Nova」"), prompt[:200]
