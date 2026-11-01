"""`GET /topics/{id}/blocks` counts the timeline only when the page does not
already hold all of it, and asks which 支线 have a turn running only for the
支线 under the page.

`total` is how many blocks the narrowed timeline holds. A read with no `limit`
is the whole timeline, and the newest page with nothing older is too: their
length is the answer, and a `COUNT(*)` over the conversation repeats it. A page
with more beyond it still counts.

Who is answering in a 支线 is read from `session_states`. It used to read every
session running on the platform for every page; now it reads the 支线 the page
draws, and nothing when the page has none.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime

from app.domain.agent.models import SessionState
from tests.integration.conftest import (
    post_message,
    post_project,
    session_auth_headers,
)
from tests.integration.test_topic_list_batches import _seed_populated_project
from tests.support.counting_session import counting_sql

_COUNT = re.compile(r"SELECT count\(\*\) AS count_1 FROM blocks")
_RUNNING = re.compile(r"FROM session_states WHERE session_states\.worker_status")
_RUNNING_IN = re.compile(r"session_states\.conversation_id = ANY")


def _count(statements: list[str], pattern: re.Pattern[str]) -> int:
    return sum(1 for s in statements if pattern.search(s))


def _page(client, url: str, headers: dict) -> tuple[dict, list[str]]:
    with counting_sql(client) as statements:
        r = client.get(url, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["data"], statements


def test_a_page_holding_the_whole_timeline_is_not_counted_again(client):
    headers = session_auth_headers("alice")
    project_id = post_project(
        client, json={"name": "Count once"}, owner="alice"
    ).json()["data"]["id"]
    room = client.post(
        "/topics", json={"project_id": project_id, "title": "Room"}, headers=headers
    ).json()["data"]["id"]
    for i in range(3):
        post_message(client, room, "alice", {"content": f"消息 {i}"})

    whole, statements = _page(client, f"/topics/{room}/blocks", headers)
    assert whole["total"] == len(whole["data"]) >= 3
    assert _count(statements, _COUNT) == 0, statements

    newest, statements = _page(client, f"/topics/{room}/blocks?limit=200", headers)
    assert newest["has_more"] is False
    assert newest["total"] == whole["total"]
    assert _count(statements, _COUNT) == 0, statements

    # A page with more beyond it still says how many there are in all.
    first, statements = _page(client, f"/topics/{room}/blocks?limit=1", headers)
    assert first["has_more"] is True
    assert first["total"] == whole["total"]
    assert _count(statements, _COUNT) == 1, statements

    older, statements = _page(
        client, f"/topics/{room}/blocks?limit=200&before={first['oldest_id']}", headers
    )
    assert older["total"] == whole["total"]
    assert _count(statements, _COUNT) == 1, statements

    # No 支线 under any of these messages: nobody is asked who is answering.
    assert _count(statements, _RUNNING) == 0, statements


def test_who_is_answering_is_read_for_the_threads_under_the_page(client):
    project_id, rooms = _seed_populated_project(client)
    headers = session_auth_headers("alice")
    page, _ = _page(client, f"/topics/{rooms[0]}/blocks", headers)
    thread_id = next(item["thread"]["id"] for item in page["data"] if "thread" in item)

    async def _running() -> None:
        async with client.test_factory() as s:
            for conversation, seat in (
                (uuid.UUID(thread_id), "cheese-answering"),
                # A turn running somewhere this page does not draw.
                (uuid.UUID(rooms[1]), "cheese-elsewhere"),
            ):
                s.add(
                    SessionState(
                        conversation_id=conversation,
                        agent_handle=seat,
                        harness="claude-code",
                        worker_epoch=1,
                        worker_status="running",
                        changed_at=datetime.now(UTC),
                    )
                )
            await s.commit()

    asyncio.run(_running())

    page, statements = _page(client, f"/topics/{rooms[0]}/blocks", headers)
    replying = {
        item["thread"]["id"]: item["thread"]["replying"]
        for item in page["data"]
        if "thread" in item
    }
    assert replying[thread_id] == ["cheese-answering"]
    running = [s for s in statements if _RUNNING.search(s)]
    assert len(running) == 1 and _RUNNING_IN.search(running[0]), running
