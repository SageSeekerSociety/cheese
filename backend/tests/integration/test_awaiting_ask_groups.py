"""The composer takeover reads the caller's open groups from the room, not the
loaded timeline window.

`GET /topics/{topic_id}/asks/awaiting` returns each open group a viewer still
owes an answer, by the registration fields the panel needs — a room the viewer
opens long after the questions were asked shows the panel from the first frame
even though the member blocks are outside the loaded window. Only the addressee
gets their own groups; a settled group stops being one.
"""

from tests.ask_fixtures import active_ask
from tests.integration.conftest import (
    in_thread,
    join_project_team,
    post_project,
    room_agent_seat,
    session_auth_headers,
)


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "周会"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return project["id"], room


def _awaiting(client, room: str, handle: str) -> list[dict]:
    response = client.get(
        f"/topics/{room}/asks/awaiting", headers=session_auth_headers(handle)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["groups"]


def test_only_the_addressees_open_groups_are_listed(client, stub_hooks, monkeypatch):
    project_id, room = _room(client)
    join_project_team(client, project_id, "bob")
    added = client.post(
        f"/topics/{room}/members",
        json={"handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert added.status_code == 200, added.text

    # 芝士在支线里回答，题也在那里问。
    room = in_thread(client, room, "alice")
    with active_ask(client, stub_hooks, monkeypatch, room, actor="alice") as headers:
        made = client.post(
            f"/topics/{room}/asks",
            json={
                "questions": [
                    {
                        "question": "分页方案选哪个？",
                        "options": [{"text": "cursor"}, {"text": "pageStart"}],
                    },
                    {
                        "question": "缓存放哪？",
                        "options": [{"text": "redis"}, {"text": "memory"}],
                    },
                ]
            },
            headers=headers,
        )
        assert made.status_code == 200, made.text
        data = made.json()["data"]
    seat = room_agent_seat(client, room)

    # The addressee sees the whole group — enough to register it without ever
    # loading its member blocks.
    (group,) = _awaiting(client, room, "alice")
    assert group["id"] == data["group"]["id"]
    assert group["asked_by"] == seat
    assert group["members"] == data["group"]["members"]
    assert group["total"] == 2
    assert group["anchor"] in group["members"]

    # A question addressed to alice is not bob's to answer.
    assert _awaiting(client, room, "bob") == []

    # Answering the whole group and settling it ends the wait.
    answered = [
        {
            "block_id": block["id"],
            "kind": "option",
            "option": block["meta"]["options"][0]["text"],
            "client_op_id": f"op-1-{index}",
            "expect_version": 0,
        }
        for index, block in enumerate(data["blocks"])
    ]
    settled = client.post(
        f"/topics/asks/{data['group']['id']}/settle",
        json={
            "topic_id": data["group"]["topic_id"],
            "asked_by": data["group"]["asked_by"],
            "client_op_id": "op-1",
            "expect_version": 0,
            "answered": answered,
            "later": [],
            "unanswered": [],
        },
        headers=session_auth_headers("alice"),
    )
    assert settled.status_code == 200, settled.text
    assert _awaiting(client, room, "alice") == []
