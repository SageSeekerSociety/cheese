"""Comments on the living document: a thread on quoted words, passive unless
it names the agent."""

import uuid

import pytest

from app.api.deps import get_handless_sessions
from app.core.sandbox_auth import mint_scoped_token
from app.domain.topic.models import Topic, TopicStatus
from app.main import app
from tests.conftest import seed_user
from tests.integration.conftest import (
    post_project,
    room_agent_seat,
)


@pytest.fixture
def sessions():
    from tests.integration.test_doc_agent import FakeSessions

    fake = FakeSessions()
    app.dependency_overrides[get_handless_sessions] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_handless_sessions, None)


def _topic(client) -> str:
    p = post_project(client, json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return t["id"]


def _member_topic(client) -> str:
    """A room whose owner is signed in: reading threads takes a real member."""
    token = seed_user(client, "commenter")
    client.headers.update({"Authorization": f"Bearer {token}"})
    project = post_project(client, {"name": "P"}, owner="commenter").json()["data"]
    return client.post(
        "/topics", json={"project_id": project["id"], "title": "T"}
    ).json()["data"]["id"]


def _threads(client, tid: str, **kw) -> list[dict]:
    return client.get(f"/topics/{tid}/comments/threads", **kw).json()["data"]["data"]


def test_comment_starts_a_thread_on_its_quote_and_is_not_in_timeline(client):
    tid = _member_topic(client)
    client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 目标\n\n做一个课程推荐系统", "expected_version": 0},
    )
    r = client.post(
        f"/topics/{tid}/comments",
        json={"quote": "课程推荐系统", "content": "这个范围要再收窄"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["kind"] == "comment"

    (thread,) = _threads(client, tid)
    assert thread["comment"]["anchor_quote"] == "课程推荐系统"
    assert thread["comment"]["content"] == "这个范围要再收窄"
    assert thread["state"] == "open"
    assert thread["replies"] == []
    # A comment is a document-view block, not a conversation message.
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert not any(b["kind"] == "comment" for b in blocks)


def test_archived_room_takes_no_comments(client):
    tid = _member_topic(client)

    async def archive():
        async with client.test_factory() as db:
            topic = await db.get(Topic, uuid.UUID(tid))
            topic.status = TopicStatus.archived
            await db.commit()

    client.portal.call(archive)
    r = client.post(f"/topics/{tid}/comments", json={"content": "还能写吗"})

    assert r.status_code == 422
    assert _threads(client, tid) == []


def test_comment_requires_content(client):
    tid = _member_topic(client)
    r = client.post(f"/topics/{tid}/comments", json={"content": "  "})
    assert r.status_code == 422


def test_human_comment_does_not_wake_ai_or_change_document(client, sessions):
    token = seed_user(client, "commenter")
    project = post_project(client, {"name": "P"}, owner="commenter").json()["data"]
    headers = {"Authorization": f"Bearer {token}"}
    tid = client.post(
        "/topics", json={"project_id": project["id"], "title": "T"}, headers=headers
    ).json()["data"]["id"]
    saved = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 原稿\n\n保留这一段", "expected_version": 0},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    before = client.get(f"/topics/{tid}/doc").json()["data"]
    nodes = client.get(f"/topics/{tid}/docs").json()["data"]["data"]
    timeline = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    result = client.post(
        f"/topics/{tid}/comments",
        json={"quote": "这一段", "content": "请改写这一段"},
        headers=headers,
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["author"] == "commenter"
    assert sessions.asked == []
    assert client.get(f"/topics/{tid}/doc").json()["data"] == before
    assert client.get(f"/topics/{tid}/docs").json()["data"]["data"] == nodes
    assert client.get(f"/topics/{tid}/blocks").json()["data"]["data"] == timeline
    assert len(_threads(client, tid, headers=headers)) == 1


def test_agent_comment_is_attributed_but_does_not_wake_itself(client, sessions):
    tid = _topic(client)
    topic = client.get(f"/topics/{tid}").json()["data"]
    token = mint_scoped_token(project_id=topic["project_id"], topic_id=tid)

    r = client.post(
        f"/topics/{tid}/comments",
        json={"content": "I already handled this."},
        headers={"X-Cheese-Token": token},
    )

    assert r.status_code == 200
    comment = r.json()["data"]
    assert comment["author"] == room_agent_seat(client, tid)
    assert sessions.asked == []
