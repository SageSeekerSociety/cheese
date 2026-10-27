"""A push that did not reach the task's branch is the machine's line in 现场.

It used to be a chat message signed by the teammate, telling the room to retry
a turn — something only the platform's next turn can do and nobody reading the
room can. The failure still has to be visible (a turn whose work stayed on the
machine looks like a turn that succeeded), so it is kept where the sync ran:
one `sync_failed` status record.

It joins the turn whose checkpoint ran, so it sits in that turn's process view
as well as the whole 现场. The turn comes from the checkpoint itself — the
platform runs the sync with `CHEESE_TURN` set to the turn that just ended —
not from whatever is newest when the note is written.

The one failure that does stay in the room is a PR already in the merge queue:
retrying cannot help there, and a person has to decide.
"""

from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)

BROWSER = {"Authorization": ""}


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


def _note(client, task_id: str) -> dict:
    site = client.get(
        f"/topics/{task_id}/transcript", headers=session_auth_headers("alice")
    )
    assert site.status_code == 200, site.text
    [record] = [
        i
        for i in site.json()["data"]["data"]
        if any(p.get("label") == "sync_failed" for p in i["meta"]["parts"])
    ]
    return record


def test_the_failed_sync_joins_the_turn_its_checkpoint_ran(client):
    """The checkpoint's own command posts this, with the turn it ended."""
    project_id, _, task_id, seat = _a_task(client)
    turn = "0f0e0d0c-0b0a-4908-8706-050403020100"
    text = "有改动没能推回 task/72508bfc——它还留在那台机器上"

    posted = _post(
        client,
        task_id,
        {**task_agent_headers(project_id, task_id, seat), "X-Cheese-Turn": turn},
        text,
    )
    assert posted.status_code == 200, posted.text

    record = _note(client, task_id)
    assert record["author"] == seat
    assert record["turn_id"] == turn
    assert record["meta"]["platform"] is True
    [status] = [p for p in record["meta"]["parts"] if p["kind"] == "status"]
    assert status == {"kind": "status", "label": "sync_failed", "text": text}

    room = client.get(
        f"/topics/{task_id}/blocks", headers=session_auth_headers("alice")
    )
    assert room.status_code == 200, room.text
    assert all(
        text not in (b.get("content") or "") for b in room.json()["data"]["data"]
    )


def test_a_sync_outside_any_turn_is_a_platform_line_of_its_own(client):
    """A commit's own sync has no turn to join: 现场 draws it ungrouped."""
    project_id, _, task_id, seat = _a_task(client)

    posted = _post(
        client, task_id, task_agent_headers(project_id, task_id, seat), "推不回去"
    )
    assert posted.status_code == 200, posted.text

    record = _note(client, task_id)
    assert record["turn_id"] is None
    assert record["meta"]["platform"] is True


def test_only_this_place_s_own_credential_may_write_one(client):
    project_id, room, task_id, seat = _a_task(client)
    text = "有改动没能推回 task/x"

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
