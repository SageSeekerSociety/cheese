"""B3: replying to a message threads the reply under it (reply_to)."""

from tests.integration.conftest import (
    post_message,
    post_project,
    room_socket,
)


def _topic(client) -> str:
    p = post_project(client, json={"name": "P"}, owner="u").json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T"},
    ).json()["data"]
    return t["id"]


def _post(client, ws, tid, content, reply_to=None):
    post_message(client, tid, "u", {"content": content, "reply_to": reply_to})
    # human-only post → user_block then done
    block = None
    while True:
        f = ws.receive_json()
        if f["type"] == "user_block":
            block = f["block"]
        if f["type"] in ("done", "error"):
            break
    return block


def test_reply_threads_under_parent(client):
    tid = _topic(client)
    with room_socket(client, tid, "u") as ws:
        parent = _post(client, ws, tid, "根消息")
        child = _post(client, ws, tid, "这是回复", reply_to=parent["id"])
    assert parent["reply_to"] is None
    assert child["reply_to"] == parent["id"]
