"""A channel's overview lists its tasks with the last thing said in each — what
someone glancing at the channel reads as where the task is now."""

from tests.integration.conftest import (
    open_task,
    post_message,
    post_project,
    session_auth_headers,
)


def test_each_task_comes_with_the_last_thing_said_in_it(client):
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    channel = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "预览"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    talked = open_task(client, channel, "微信预览", start=False)["id"]
    quiet = open_task(client, channel, "访问口令", start=False)["id"]
    post_message(client, talked, "alice", {"content": "先看 iframe"})
    post_message(client, talked, "alice", {"content": "改成直接打开预览页"})

    rows = client.get(
        f"/topics/{channel}/tasks",
        params={"limit": 1},
        headers=session_auth_headers("alice"),
    ).json()["data"]["data"]
    by_id = {row["id"]: row for row in rows}

    assert by_id[talked]["last_message"]["content"] == "改成直接打开预览页"
    assert by_id[quiet]["last_message"] is None
