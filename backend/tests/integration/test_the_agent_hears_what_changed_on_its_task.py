"""Whatever a person can see change on a task, its AI teammate learns too.

Otherwise the two talk past each other: someone says 「作废了，重新递一版」
while the teammate still believes its card is waiting on review. A change
someone else made reaches the teammate one of two ways: one it has to act on
wakes its session with an instruction; one it only has to know about rides
into its next input and wakes nothing.
"""

from tests.ask_fixtures import wait_turn_idle
from tests.conftest import wait_work_idle
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import (
    join_project_team,
    new_project,
    open_task,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)


def _until_done(ws) -> None:
    while True:
        frame = ws.receive_json()
        if frame["type"] == "error":
            raise AssertionError(frame)
        if frame["type"] == "done":
            return


def _filed_card(client) -> tuple[str, str]:
    """A card filed by a task's teammate, with alice as its reviewer."""
    pid = post_project(client, json={"name": "P"}).json()["data"]["id"]
    join_project_team(client, pid, "alice")
    room = client.post("/topics", json={"project_id": pid, "title": "做一个东西"})
    room_id = room.json()["data"]["id"]
    card = client.post(
        f"/topics/{delivery_task_id(client, room_id)}/accept-card",
        headers=delivery_headers(client, room_id),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "focus": "最懂",
        },
    )
    assert card.status_code == 200, card.text
    return room_id, card.json()["data"]["id"]


def test_voiding_a_card_wakes_the_teammate_with_who_and_why(client, stub_hooks):
    _room, card = _filed_card(client)
    wait_work_idle()
    stub_hooks.last_prompt = None

    voided = client.post(
        f"/accept-cards/{card}/void",
        json={"note": "这一版的口径不对，按新口径重做"},
        headers=session_auth_headers("alice"),
    )
    assert voided.status_code == 200, voided.text
    wait_work_idle()

    heard = stub_hooks.last_prompt or ""
    assert "这一版的口径不对，按新口径重做" in heard
    assert "@alice 作废" in heard


def _task(client):
    project = new_project(client, owner="alice")
    task = open_task(client, project["root_topic_id"], owner="alice")["id"]
    wait_turn_idle(client, task)
    return task


def _say(client, task, text: str) -> None:
    """alice speaks in the task and the turn her message opens runs out."""
    with room_socket(client, task, "alice") as ws:
        post_message(client, task, "alice", {"content": text})
        _until_done(ws)


def test_a_person_reopening_a_task_reaches_the_teammates_next_input(client, stub_hooks):
    task = _task(client)
    closed = client.post(
        f"/topics/{task}/close",
        json={"conclusion": "先放一放"},
        headers=session_auth_headers("alice"),
    )
    assert closed.status_code == 200, closed.text
    reopened = client.post(
        f"/topics/{task}/reopen", headers=session_auth_headers("alice")
    )
    assert reopened.status_code == 200, reopened.text
    wait_work_idle()

    _say(client, task, "接着做吧")

    heard = stub_hooks.last_prompt or ""
    assert "@alice 重新打开" in heard


def test_a_change_it_only_has_to_know_about_waits_for_its_next_input(
    client, stub_hooks
):
    """A person renaming the task wakes nothing; the teammate reads it in the
    next input a person's message opens."""
    task = _task(client)
    stub_hooks.last_prompt = None

    renamed = client.post(
        f"/topics/{task}/title",
        json={"title": "按部门统计的月报"},
        headers=session_auth_headers("alice"),
    )
    assert renamed.status_code == 200, renamed.text
    wait_work_idle()
    assert stub_hooks.last_prompt is None

    _say(client, task, "看一下名字")

    assert "按部门统计的月报" in (stub_hooks.last_prompt or "")
