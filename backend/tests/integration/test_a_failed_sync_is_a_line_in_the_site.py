"""A push that did not reach the task's branch is the machine's line in 现场.

It used to be a chat message signed by the teammate, telling the room to retry
a turn — something only the platform's next turn can do and nobody reading the
room can. The failure still has to be visible (a turn whose work stayed on the
machine looks like a turn that succeeded), so it is kept where the turn is:
one `sync_failed` status record.

The one failure that does stay in the room is a PR already in the merge queue:
retrying cannot help there, and a person has to decide.
"""

import uuid

from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)

BROWSER = {"X-Cheese-Token": ""}


def _a_task(client) -> tuple[str, str, str, str]:
    project = post_project(client, {"name": "Sync"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    seat = room_agent_seat(client, room)
    task = open_task(client, room, "同步失败", owner="alice")
    return project["id"], room, task["id"], seat


def _post(client, task_id: str, headers: dict, text: str):
    return client.post(
        f"/topics/{task_id}/session-records",
        json={"subtype": "sync_failed", "text": text},
        headers=headers,
    )


def test_the_failed_sync_is_beside_the_turn_and_not_a_message(client):
    project_id, _, task_id, seat = _a_task(client)
    turn = uuid.uuid4()
    text = "这轮的改动没能推回 task/72508bfc——它还留在那台机器上"

    posted = _post(
        client,
        task_id,
        {**task_agent_headers(project_id, task_id, seat), "X-Cheese-Turn": str(turn)},
        text,
    )
    assert posted.status_code == 200, posted.text

    site = client.get(
        f"/topics/{task_id}/transcript", headers=session_auth_headers("alice")
    )
    assert site.status_code == 200, site.text
    ours = [i for i in site.json()["data"]["data"] if i["turn_id"] == str(turn)]
    assert len(ours) == 1, ours
    [record] = ours
    assert record["author"] == seat
    parts = record["meta"]["parts"]
    [status] = [p for p in parts if p["kind"] == "status"]
    assert status == {"kind": "status", "label": "sync_failed", "text": text}

    room = client.get(
        f"/topics/{task_id}/blocks", headers=session_auth_headers("alice")
    )
    assert room.status_code == 200, room.text
    assert all(text not in (b.get("content") or "") for b in room.json()["data"]["data"])


def test_only_this_place_s_own_credential_may_write_one(client):
    project_id, room, task_id, seat = _a_task(client)
    text = "这轮的改动没能推回 task/x"

    person = _post(client, task_id, {**BROWSER, **session_auth_headers("alice")}, text)
    assert person.status_code == 401, person.text

    other = open_task(client, room, "另一件", owner="alice")
    elsewhere = _post(
        client,
        task_id,
        task_agent_headers(project_id, other["id"], seat),
        text,
    )
    assert elsewhere.status_code == 401, elsewhere.text
