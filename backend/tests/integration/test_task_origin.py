"""Where a task came from, as its page shows it (相关) and its AI teammate is
told: the discussion it was made from, and what was put on the table there.

- a task made from a message knows that message and its 支线;
- the files referred to in that discussion are listed, each once;
- the AI teammate drafting the document is handed the same list;
- a task created on its own came from nowhere.
"""

import asyncio
import uuid

from sqlalchemy import select

from tests.integration.conftest import (
    post_message,
    post_project,
    session_auth_headers,
)


def _channel(client):
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    r = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "预览"},
        headers=session_auth_headers("alice"),
    )
    return r.json()["data"]["id"]


def _related(client, task):
    r = client.get(f"/topics/{task}/related", headers=session_auth_headers("alice"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _task_from_discussion(client):
    channel = _channel(client)
    root = post_message(
        client, channel, "alice", {"content": "白屏截图在这 <&uploads/white.png>"}
    )
    thread = client.post(
        f"/blocks/{root['id']}/thread", headers=session_auth_headers("alice")
    ).json()["data"]["id"]
    post_message(
        client,
        thread,
        "alice",
        {"content": "还有一张 <&uploads/wechat.png>，同一张 <&uploads/white.png>"},
    )
    r = client.post(
        f"/blocks/{root['id']}/upgrade", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return channel, thread, root, r.json()["data"]["id"]


def test_a_task_knows_the_discussion_and_its_files(client):
    _, thread, root, task = _task_from_discussion(client)

    related = _related(client, task)

    assert related["origin"]["conversation_id"] == thread
    assert related["origin"]["root"]["block_id"] == root["id"]
    assert related["origin"]["reply_count"] == 1
    assert [m["path"] for m in related["materials"]] == [
        "uploads/white.png",
        "uploads/wechat.png",
    ]


def test_the_teammate_drafting_the_document_is_handed_the_files(client):
    from app.domain.delivery.models import Delivery

    _, _, _, task = _task_from_discussion(client)

    async def _opening():
        async with client.test_factory() as session:
            return await session.scalar(
                select(Delivery.payload).where(
                    Delivery.conversation_id == uuid.UUID(task)
                )
            )

    content = asyncio.run(_opening())["content"]
    assert "uploads/white.png" in content and "uploads/wechat.png" in content


def test_a_task_created_on_its_own_came_from_nowhere(client):
    channel = _channel(client)
    r = client.post(
        f"/topics/{channel}/tasks",
        json={"title": "整理周报"},
        headers=session_auth_headers("alice"),
    )

    assert _related(client, r.json()["data"]["id"]) == {"origin": None, "materials": []}
