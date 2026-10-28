"""A question's recorded outcome, not later talk, controls its waiting markers."""

import uuid

import pytest

from tests.ask_fixtures import active_ask, wait_turn_idle
from tests.conftest import seed_user
from tests.integration.conftest import (
    join_project_team,
    new_project,
    open_task,
    post_message,
    room_socket,
    session_auth_headers,
    task_agent_headers,
)


def _setup(client):
    project = new_project(client, owner="alice")
    for handle in ("bob", "carol"):
        seed_user(client, handle)
        join_project_team(client, project["id"], handle)
    room = project["root_topic_id"]
    task = open_task(client, room, owner="alice", contributors=["bob"])["id"]
    return project["id"], room, task


def _ask(client, stub_hooks, monkeypatch, task, *, to=("alice",), question_count=1):
    with active_ask(
        client, stub_hooks, monkeypatch, task, actor="alice", platform_turn=to is None
    ) as headers:
        body = {
            "questions": [
                {
                    "question": f"预算按哪个口径统计（{i + 1}）？",
                    "options": [{"text": "按部门"}, {"text": "按项目"}],
                }
                for i in range(question_count)
            ]
        }
        if to is not None:
            body["to"] = list(to)
        response = client.post(f"/topics/{task}/asks", json=body, headers=headers)
        assert response.status_code == 200, response.text
    wait_turn_idle(client, task)
    return response.json()["data"]["blocks"][0]


def _task_rows(client, project, room, task, handle="alice"):
    headers = session_auth_headers(handle)
    rows = []
    for path in (f"/projects/{project}/tasks", f"/topics/{room}/tasks?limit=0"):
        response = client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        rows.append(
            next(row for row in response.json()["data"]["data"] if row["id"] == task)
        )
    response = client.get(f"/topics/{task}/task", headers=headers)
    assert response.status_code == 200, response.text
    rows.append(response.json()["data"])
    with room_socket(client, task, handle) as ws:
        rows.append(ws.subscribed["room"]["task"])
    response = client.get(f"/topics/{room}", headers=headers)
    assert response.status_code == 200, response.text
    sidebar = response.json()["data"]["my_tasks"]["shown"]
    if any(row["id"] == task for row in sidebar):
        rows.append(next(row for row in sidebar if row["id"] == task))
    return rows


def _question(client, task, question):
    response = client.get(
        f"/topics/{task}/blocks", headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text
    return next(
        row for row in response.json()["data"]["data"] if row["id"] == question["id"]
    )


def _still_waits(client, project, room, task, question):
    assert _question(client, task, question)["meta"]["answer_log"] == []
    for row in _task_rows(client, project, room, task):
        assert row["awaits_me"] is True
        assert row["needs_my_action"] is True
        assert row["presentation"]["phrase"] == "awaiting_answer"


def test_talking_to_someone_else_does_not_clear_a_task_question(
    client, stub_hooks, monkeypatch
):
    project, room, task = _setup(client)
    question = _ask(client, stub_hooks, monkeypatch, task)

    post_message(client, task, "alice", {"content": "<@bob> 你那边准备好了吗？"})
    wait_turn_idle(client, task)

    _still_waits(client, project, room, task, question)
    post_message(
        client, task, "alice", {"content": "按部门", "reply_to": question["id"]}
    )
    wait_turn_idle(client, task)
    for row in _task_rows(client, project, room, task):
        assert row["awaits_me"] is False
        assert row["needs_my_action"] is False


@pytest.mark.parametrize("question_count", [1, 3])
def test_an_agents_followup_does_not_answer_its_task_question(
    client, stub_hooks, monkeypatch, question_count
):
    project, room, task = _setup(client)
    question = _ask(
        client, stub_hooks, monkeypatch, task, question_count=question_count
    )

    response = client.post(
        f"/topics/{task}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "我先把能查的查了"},
        headers=task_agent_headers(project, task, question["author"]),
    )
    assert response.status_code == 200, response.text

    _still_waits(client, project, room, task, question)


@pytest.mark.parametrize("to", [("alice", "bob"), None])
def test_each_task_view_identifies_every_person_waiting_on_a_question(
    client, stub_hooks, monkeypatch, to
):
    project, room, task = _setup(client)
    _ask(client, stub_hooks, monkeypatch, task, to=to)

    for handle, expected in (("alice", True), ("bob", True), ("carol", False)):
        for row in _task_rows(client, project, room, task, handle):
            assert row["presentation"]["phrase"] == "awaiting_answer"
            assert row["needs_my_action"] is expected
            assert row["awaits_me"] is expected, (handle, row)


def test_skipping_a_group_question_removes_only_that_persons_wait(
    client, stub_hooks, monkeypatch
):
    project, room, task = _setup(client)
    question = _ask(client, stub_hooks, monkeypatch, task, to=("alice", "bob"))
    response = client.post(
        f"/blocks/{question['id']}/skip", headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text

    for handle, expected in (("alice", False), ("bob", True)):
        for row in _task_rows(client, project, room, task, handle):
            assert row["needs_my_action"] is expected
            assert row["awaits_me"] is expected


def test_a_reporter_keeps_attention_without_being_named_as_the_answerer(
    client, stub_hooks, monkeypatch
):
    project, room, task = _setup(client)
    headers = session_auth_headers("alice")
    response = client.post(
        f"/topics/{task}/close",
        json={"reporter_handle": "carol"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    response = client.post(f"/topics/{task}/reopen", headers=headers)
    assert response.status_code == 200, response.text
    wait_turn_idle(client, task)
    question = _ask(client, stub_hooks, monkeypatch, task, to=("bob",))
    response = client.post(
        f"/blocks/{question['id']}/skip", headers=session_auth_headers("carol")
    )
    assert response.status_code == 403, response.text

    for row in _task_rows(client, project, room, task, "carol"):
        assert row["awaits_me"] is True
        assert row["needs_my_action"] is False
    for row in _task_rows(client, project, room, task, "bob"):
        assert row["awaits_me"] is True
        assert row["needs_my_action"] is True


@pytest.mark.parametrize("mutation", ["rename", "collaborators"])
def test_updating_a_task_keeps_the_callers_personal_question_marker(
    client, stub_hooks, monkeypatch, mutation
):
    project, room, task = _setup(client)
    question = _ask(client, stub_hooks, monkeypatch, task, to=("alice", "bob"))
    headers = session_auth_headers("alice")
    if mutation == "rename":
        response = client.post(
            f"/topics/{task}/title", json={"title": "预算统计口径"}, headers=headers
        )
    else:
        response = client.patch(
            f"/topics/{task}/task", json={"contributor_handles": []}, headers=headers
        )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["awaits_me"] is True
    assert response.json()["data"]["needs_my_action"] is True
    _still_waits(client, project, room, task, question)


def test_taking_back_an_answer_reopens_the_question_in_every_task_view(
    client, stub_hooks, monkeypatch
):
    project, room, task = _setup(client)
    question = _ask(client, stub_hooks, monkeypatch, task)
    post_message(
        client, task, "alice", {"content": "按部门", "reply_to": question["id"]}
    )
    wait_turn_idle(client, task)
    response = client.delete(
        f"/blocks/{question['id']}/answer", headers=session_auth_headers("alice")
    )
    assert response.status_code == 200, response.text
    wait_turn_idle(client, task)

    _still_waits(client, project, room, task, question)
