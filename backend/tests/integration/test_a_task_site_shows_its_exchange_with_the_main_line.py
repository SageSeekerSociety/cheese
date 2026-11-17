"""What the main line told a task, and what the task reported back, is in the
task's 现场 for everyone who can see the task.

Neither is said in any conversation; the 现场 is where a member reads it, in
order, as the words each side said, with who said it.
"""

from tests.integration.conftest import (
    join_project_team,
    mint_scoped_token,
    post_message,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _as(client, project, conversation, seat, method, path, body=None):
    token = mint_scoped_token(
        project_id=project, topic_id=conversation, agent_handle=seat
    )
    bearer = client.headers.pop("Authorization", None)
    try:
        return client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        if bearer is not None:
            client.headers["Authorization"] = bearer


def test_the_task_site_shows_what_the_main_line_told_it_and_what_it_reported(
    client, stub_hooks
):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, project["id"], "bob")
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "开发"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    seat = room_agent_seat(client, room)
    post_message(client, room, "alice", {"content": f"<@{seat}> 把登录页做好"})
    task = _as(
        client,
        project["id"],
        room,
        seat,
        "POST",
        f"/topics/{room}/teammate-tasks",
        {"title": "登录页", "summary": "把登录页做好", "owner_handle": "alice"},
    ).json()["data"]
    told = _as(
        client,
        project["id"],
        room,
        seat,
        "POST",
        f"/topics/{room}/tasks/{task['number']}/tell",
        {"content": "按钮改成蓝色", "asker": "bob"},
    )
    assert told.status_code == 200, told.text
    reported = _as(
        client,
        project["id"],
        task["id"],
        seat,
        "POST",
        f"/topics/{task['id']}/report",
        {"kind": "question", "content": "蓝色用哪一个色号？"},
    )
    assert reported.status_code == 200, reported.text

    site = client.get(
        f"/topics/{task['id']}/transcript", headers=session_auth_headers("bob")
    )

    assert site.status_code == 200, site.text
    exchange = [
        (row["meta"]["exchange"], row["author"], row["content"])
        for row in site.json()["data"]["data"]
        if row["meta"].get("exchange")
    ]
    assert exchange == [
        ("told", seat, "按钮改成蓝色"),
        ("report", seat, "蓝色用哪一个色号？"),
    ]
