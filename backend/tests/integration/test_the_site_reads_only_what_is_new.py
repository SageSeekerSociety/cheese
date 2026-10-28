"""现场 can be asked for what was written after a cursor it already holds.

The panel refreshes every three seconds while it is open. Asking for the
newest page each time hands it a whole page of records it already has;
asking for what came after its newest one hands it the handful that are new.
These tests pin that direction: only the newer rows, oldest first, one page
after another until nothing is left — and the reader's upward cursor left
alone, since this direction says nothing about what is above the window.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.domain.agent.models import AgentTurn
from app.domain.conversation.models import Conversation
from app.domain.session_record.models import SessionRecord
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
    session_auth_headers,
)

HEADERS = {"headers": session_auth_headers("alice")}


def _room(client) -> str:
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    return client.post(
        "/topics",
        json={"project_id": project["id"], "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]


def _records(client, room_id: str, count: int) -> None:
    """One run of `count` records, a second apart, each naming its place."""
    author = room_agent_seat(client, room_id)

    async def add() -> None:
        async with client.test_request_factory() as db:
            start = datetime.now(UTC)
            db.add(
                SessionRecord(
                    conversation_id=uuid.UUID(room_id),
                    agent_handle=author,
                    harness="claude_code",
                    first_sequence=1,
                    last_sequence=count,
                    first_at=start,
                    last_at=start + timedelta(seconds=count),
                    count=count,
                    bytes=count,
                    records=[
                        {
                            "sequence": i,
                            "at": (start + timedelta(seconds=i)).isoformat(),
                            "record": {
                                "type": "user",
                                "message": {
                                    "content": [
                                        {"type": "text", "text": f"step {i:03d}"}
                                    ]
                                },
                            },
                        }
                        for i in range(1, count + 1)
                    ],
                )
            )
            await db.commit()

    client.portal.call(add)


def _site(client, topic: str, **params) -> dict:
    response = client.get(f"/topics/{topic}/transcript", params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_a_forward_read_returns_only_what_came_after_the_cursor(client):
    room = _room(client)
    _records(client, room, 6)
    newest = _site(client, room, limit=3)
    cursor = newest["data"][0]["meta"]["cursor"]  # the oldest row on that page

    newer = _site(client, room, limit=100, after=cursor)

    assert [b["id"] for b in newer["data"]] == [b["id"] for b in newest["data"]][1:]


def test_walking_forward_reads_each_new_record_once(client):
    room = _room(client)
    _records(client, room, 10)
    whole = _site(client, room, limit=100)
    cursor = whole["data"][0]["meta"]["cursor"]  # the oldest record there is
    seen: list[str] = []

    for _ in range(10):
        page = _site(client, room, limit=3, after=cursor)
        # 这一路只说「下面还有没有更新的」，不动读者手上的往上翻游标。
        assert page["has_more"] is False
        assert page["oldest_id"] is None
        seen.extend(b["id"] for b in page["data"])
        if not page["has_newer"]:
            break
        cursor = page["newest_id"]

    assert seen == [b["id"] for b in whole["data"]][1:], "每一条各出现一次，按顺序"


def test_asking_after_the_newest_record_reads_nothing(client):
    room = _room(client)
    _records(client, room, 3)
    newest = _site(client, room, limit=100)["data"][-1]["meta"]["cursor"]

    newer = _site(client, room, limit=100, after=newest)

    assert newer["data"] == []
    assert newer["has_newer"] is False
    # 空的一页也把原游标还回去：读者追平以后手上不能没有游标，否则下一次只能整页
    # 重读——正是这一路要消掉的那件事。
    assert newer["newest_id"] == newest


def test_forward_reaches_the_second_of_two_turns_that_started_together(client):
    """两个启动前的轮次同一时刻开始的，往前读也不能漏掉后一个。

    这一路按游标排，而游标里带着轮次 id：只比时刻的话，从第一个往后读就永远读不到
    第二个（反过来往上翻会把它读两遍）。"""
    room = _room(client)
    seat = room_agent_seat(client, room)
    start = datetime.now(UTC) - timedelta(hours=2)

    async def add_turns() -> None:
        async with client.test_request_factory() as db:
            # 启动前的轮次：没有会话记录，且都在 raw_records_started_at 之前。
            await db.execute(
                update(Conversation)
                .where(Conversation.id == uuid.UUID(room))
                .values(raw_records_started_at=datetime.now(UTC) - timedelta(hours=1))
            )
            for _ in range(2):
                turn = uuid.uuid4()
                db.add(
                    AgentTurn(
                        id=turn,
                        conversation_id=uuid.UUID(room),
                        continuation_id=turn,
                        author="teammate",
                        agent_handle=seat,
                        started_at=start,
                        stopped_at=start + timedelta(minutes=1),
                    )
                )
            await db.commit()

    client.portal.call(add_turns)

    both = _site(client, room, limit=100)["data"]
    assert len(both) == 2, both
    first = both[0]["meta"]["cursor"]

    forward = _site(client, room, limit=100, after=first)["data"]

    assert [b["id"] for b in forward] == [both[1]["id"]], "第二个轮次还在这一路上"


def test_a_cursor_that_is_not_one_of_ours_is_refused(client):
    room = _room(client)

    response = client.get(
        f"/topics/{room}/transcript",
        params={"after": "not-a-cursor"},
        headers=HEADERS["headers"],
    )

    assert response.status_code == 404
