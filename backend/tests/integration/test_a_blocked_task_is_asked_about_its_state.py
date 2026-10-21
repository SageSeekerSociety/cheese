"""A task whose teammate last reported 「卡住了」 is asked about it when a person
speaks in it.

「卡住了」 is usually the last thing a turn says, and the stale-report reminder
(`StaleProgressReminder`) only reaches a session that is running — by the time
someone answers, that turn is over. So the question is put in front of it at
the moment it is back at work: the turn a person's message opens. It is asked,
not decided: whether it can carry on now is the teammate's to say.
"""

from app.domain.agent.harness.notices import blocked_report_reminder
from tests.ask_fixtures import wait_turn_idle
from tests.integration.conftest import (
    new_project,
    open_task,
    post_message,
    room_agent_seat,
    room_socket,
    task_agent_headers,
)


def _until_done(ws) -> None:
    while True:
        frame = ws.receive_json()
        if frame["type"] == "error":
            raise AssertionError(frame)
        if frame["type"] == "done":
            return


def _reported(client, project, task, seat, state: str) -> None:
    response = client.put(
        f"/topics/{task}/task/progress",
        json={"state": state, "note": "在等负责人回答"},
        headers=task_agent_headers(project["id"], task, seat),
    )
    assert response.status_code == 200, response.text


def _task(client, project):
    room = project["root_topic_id"]
    task = open_task(client, room, owner="alice")["id"]
    seat = room_agent_seat(client, room)
    wait_turn_idle(client, task)
    return task, seat


def test_a_person_speaking_in_a_blocked_task_is_asked_to_report_again(
    client, stub_hooks
):
    project = new_project(client, owner="alice")
    task, seat = _task(client, project)
    _reported(client, project, task, seat, "blocked")

    with room_socket(client, task, "alice") as ws:
        post_message(client, task, "alice", {"content": "按部门"})
        _until_done(ws)

    heard = stub_hooks.last_prompt or ""
    assert blocked_report_reminder(None) in heard


def test_a_task_that_is_working_is_not_asked(client, stub_hooks):
    project = new_project(client, owner="alice")
    task, seat = _task(client, project)
    _reported(client, project, task, seat, "working")

    with room_socket(client, task, "alice") as ws:
        post_message(client, task, "alice", {"content": "按部门"})
        _until_done(ws)

    heard = stub_hooks.last_prompt or ""
    assert blocked_report_reminder(None) not in heard
