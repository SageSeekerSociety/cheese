"""A push that did not reach the task's branch is the machine's line in 现场.

It used to be a chat message signed by the teammate, telling the room to retry
a turn — something only the platform's next turn can do and nobody reading the
room can. The failure still has to be visible (a turn whose work stayed on the
machine looks like a turn that succeeded), so it is kept where the turn is:
one `sync_failed` status record.

The one failure that does stay in the room is a PR already in the merge queue:
retrying cannot help there, and a person has to decide.

The machine is given no turn id, so the note joins the turn whose checkpoint
it is — the newest turn the conversation's records carry. Without that it
would render as a turn of its own with no steps, and a reader looking at one
turn's 过程 would not see it at all.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.domain.session_record.models import SessionRecord
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
    session_auth_headers,
    task_agent_headers,
)

BROWSER = {"X-Cheese-Token": ""}


def _seed_turn(client, task_id: str, turn: uuid.UUID, seat: str) -> None:
    """The harness's own last record of a turn, as the runner journals it."""

    async def write() -> None:
        at = datetime.now(UTC)
        async with client.test_factory() as session:
            session.add(
                SessionRecord(
                    conversation_id=uuid.UUID(task_id),
                    turn_id=turn,
                    agent_handle=seat,
                    harness="claude-code",
                    first_sequence=1,
                    last_sequence=1,
                    first_at=at,
                    last_at=at,
                    count=1,
                    bytes=1,
                    records=[
                        {
                            "sequence": 1,
                            "at": at.isoformat(),
                            "record": {"type": "assistant"},
                        }
                    ],
                )
            )
            await session.commit()

    asyncio.run(write())


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
    assert all(
        text not in (b.get("content") or "") for b in room.json()["data"]["data"]
    )


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


def test_a_note_without_a_turn_joins_the_turn_it_belongs_to(client):
    project_id, _, task_id, seat = _a_task(client)
    turn = uuid.uuid4()
    _seed_turn(client, task_id, turn, seat)

    posted = _post(
        client, task_id, task_agent_headers(project_id, task_id, seat), "推不回去"
    )
    assert posted.status_code == 200, posted.text

    raw = client.get(
        f"/topics/{task_id}/session-records", headers=session_auth_headers("alice")
    )
    assert raw.status_code == 200, raw.text
    [note] = [
        i
        for i in raw.json()["data"]["items"]
        if i["record"].get("subtype") == "sync_failed"
    ]
    assert note["turn_id"] == str(turn)
