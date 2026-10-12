"""A task's card (`/topics/{task}/map-card`): who reads it and who changes it.
Where a task came from is its origin's (`test_a_task_says_where_it_came_from.py`).

- the card the task's own session writes is the one the task page reads;
- someone outside the project neither reads nor changes it;
- a project member not working the task reads it and cannot change it, while
  the task's owner can;
- a task nobody has written a card for reads as having none;
- a person named under 等谁 has to be in the project.
"""

from tests.integration.conftest import (
    join_project_team,
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)


def _task(client, *, start: bool = False) -> tuple[str, str, str]:
    project = post_project(client, {"name": "Map"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "失物招领"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    task = open_task(client, room, "认领按钮", owner="alice", start=start)
    return project["id"], room, task["id"]


def _read(client, task, headers):
    return client.get(f"/topics/{task}/map-card", headers=headers)


def _change(client, task, headers, body):
    return client.patch(f"/topics/{task}/map-card", json=body, headers=headers)


def test_the_card_the_task_s_session_writes_is_what_its_page_reads(client):
    project, room, task = _task(client)
    seat = room_agent_seat(client, room)
    join_project_team(client, project, "bob")

    written = _change(
        client,
        task,
        task_agent_headers(project, task, seat),
        {
            "what": "丢东西的人点「是我的」，就能联系到捡到的人",
            "progress": "表设计好了，在写这一步",
            "waiting_on": [{"handle": "@bob", "ask": "看一下表结构"}],
            "terms": [{"term": "认领", "meaning": "失主确认东西是自己的"}],
        },
    )
    assert written.status_code == 200, written.text

    page = _read(client, task, session_auth_headers("alice"))
    assert page.status_code == 200, page.text
    card = page.json()["data"]["card"]
    assert card["what"] == "丢东西的人点「是我的」，就能联系到捡到的人"
    assert card["progress"] == "表设计好了，在写这一步"
    assert card["waiting_on"] == [{"handle": "bob", "ask": "看一下表结构"}]
    assert card["terms"] == [{"term": "认领", "meaning": "失主确认东西是自己的"}]
    assert card["updated_by"] == seat

    # A later change touches only what it names.
    _change(
        client,
        task,
        task_agent_headers(project, task, seat),
        {"progress": "写完了，等人看", "waiting_on": []},
    )
    card = _read(client, task, session_auth_headers("alice")).json()["data"]["card"]
    assert card["progress"] == "写完了，等人看"
    assert card["waiting_on"] == []
    assert card["what"] == "丢东西的人点「是我的」，就能联系到捡到的人"


def test_someone_outside_the_project_neither_reads_nor_changes_the_card(client):
    _project, _room, task = _task(client)
    _change(client, task, session_auth_headers("alice"), {"what": "内部的事"})

    outsider = session_auth_headers("mallory")
    assert _read(client, task, outsider).status_code in (403, 404)
    refused = _change(client, task, outsider, {"what": "改掉"})
    assert refused.status_code in (403, 404), refused.text

    card = _read(client, task, session_auth_headers("alice")).json()["data"]["card"]
    assert card["what"] == "内部的事"


def test_a_member_not_working_the_task_reads_the_card_but_cannot_change_it(client):
    project, _room, task = _task(client)
    join_project_team(client, project, "bob")
    _change(client, task, session_auth_headers("alice"), {"what": "负责人写的"})

    bob = session_auth_headers("bob")
    seen = _read(client, task, bob)
    assert seen.status_code == 200, seen.text
    assert seen.json()["data"]["card"]["what"] == "负责人写的"
    assert seen.json()["data"]["can_edit"] is False
    assert _change(client, task, bob, {"what": "bob 改的"}).status_code == 403

    owner = _read(client, task, session_auth_headers("alice")).json()["data"]
    assert owner["can_edit"] is True
    assert owner["card"]["what"] == "负责人写的"


def test_a_task_nobody_wrote_a_card_for_has_none(client):
    _project, _room, task = _task(client)
    view = _read(client, task, session_auth_headers("alice")).json()["data"]
    assert view["card"] is None


def test_a_person_waited_on_has_to_be_in_the_project(client):
    _project, _room, task = _task(client)
    refused = _change(
        client,
        task,
        session_auth_headers("alice"),
        {"waiting_on": [{"handle": "nobody-here", "ask": "看一下"}]},
    )
    assert refused.status_code == 422, refused.text
    assert (
        _read(client, task, session_auth_headers("alice")).json()["data"]["card"]
        is None
    )
