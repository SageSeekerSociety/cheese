"""Comments on a task's living document: a thread on quoted words, passive
unless it names the agent."""

import pytest

from app.api.deps import (
    consumptions_for,
    get_chat_service,
    get_consumptions,
    get_session_host,
)
from app.core.sandbox_auth import mint_scoped_token
from app.main import app
from tests.conftest import seed_user
from tests.integration.conftest import (
    open_task,
    post_project,
    room_agent_seat,
)
from tests.support.living_doc import document_of


def _chat():
    """The chat service the app is serving this test with."""
    return app.dependency_overrides.get(get_chat_service, get_chat_service)()


@pytest.fixture
def sessions():
    from tests.integration.test_doc_agent import FakeSessions

    fake = FakeSessions()
    questions = consumptions_for(fake, _chat())  # type: ignore[arg-type]
    app.dependency_overrides[get_session_host] = lambda: fake
    app.dependency_overrides[get_consumptions] = lambda: questions
    yield fake
    app.dependency_overrides.pop(get_session_host, None)
    app.dependency_overrides.pop(get_consumptions, None)


def _topic(client) -> tuple[str, dict]:
    """A room, and a task in it: the room's agent works the task."""
    p = post_project(client, json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return t["id"], open_task(client, t["id"], owner="owner", start=False)


def _member_topic(client) -> str:
    """A task whose owner is signed in: reading threads takes a real member."""
    token = seed_user(client, "commenter")
    client.headers.update({"Authorization": f"Bearer {token}"})
    project = post_project(client, {"name": "P"}, owner="commenter").json()["data"]
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "T"}
    ).json()["data"]["id"]
    return open_task(client, room, owner="commenter", start=False)["id"]


def _threads(client, doc: str, **kw) -> list[dict]:
    return client.get(f"/documents/{doc}/comments/threads", **kw).json()["data"]["data"]


def test_comment_starts_a_thread_on_its_quote_and_is_not_in_timeline(client):
    tid = _member_topic(client)
    doc = document_of(client, tid)
    client.put(
        f"/documents/{doc}",
        json={"content": "# 目标\n\n做一个课程推荐系统", "expected_version": 0},
    )
    r = client.post(
        f"/documents/{doc}/comments",
        json={"quote": "课程推荐系统", "content": "这个范围要再收窄"},
    )
    assert r.status_code == 200

    (thread,) = _threads(client, doc)
    assert thread["comment"]["id"] == r.json()["data"]["id"]
    assert thread["comment"]["anchor_quote"] == "课程推荐系统"
    assert thread["comment"]["content"] == "这个范围要再收窄"
    assert thread["state"] == "open"
    assert thread["replies"] == []
    # A comment is on the document, not a message in the task's conversation.
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert not any("这个范围要再收窄" in b["content"] for b in blocks)


def test_a_closed_task_takes_no_comments(client):
    tid = _member_topic(client)
    doc = document_of(client, tid)

    assert client.post(f"/topics/{tid}/close", json={}).status_code == 200
    r = client.post(f"/documents/{doc}/comments", json={"content": "还能写吗"})

    assert r.status_code == 422
    assert _threads(client, doc) == []


def test_comment_requires_content(client):
    doc = document_of(client, _member_topic(client))
    r = client.post(f"/documents/{doc}/comments", json={"content": "  "})
    assert r.status_code == 422


def test_human_comment_does_not_wake_ai_or_change_document(client, sessions):
    token = seed_user(client, "commenter")
    project = post_project(client, {"name": "P"}, owner="commenter").json()["data"]
    headers = {"Authorization": f"Bearer {token}"}
    room = client.post(
        "/topics", json={"project_id": project["id"], "title": "T"}, headers=headers
    ).json()["data"]["id"]
    tid = open_task(client, room, owner="commenter", start=False)["id"]
    doc = document_of(client, tid, headers=headers)
    saved = client.put(
        f"/documents/{doc}",
        json={"content": "# 原稿\n\n保留这一段", "expected_version": 0},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    before = client.get(f"/documents/{doc}").json()["data"]
    nodes = client.get(f"/documents/{doc}/nodes").json()["data"]["data"]
    timeline = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    result = client.post(
        f"/documents/{doc}/comments",
        json={"quote": "这一段", "content": "请改写这一段"},
        headers=headers,
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["author"] == "commenter"
    assert sessions.asked == []
    assert client.get(f"/documents/{doc}").json()["data"] == before
    assert client.get(f"/documents/{doc}/nodes").json()["data"]["data"] == nodes
    assert client.get(f"/topics/{tid}/blocks").json()["data"]["data"] == timeline
    assert len(_threads(client, doc, headers=headers)) == 1


def test_agent_comment_is_attributed_but_does_not_wake_itself(client, sessions):
    room, task = _topic(client)
    token = mint_scoped_token(project_id=task["project_id"], topic_id=task["id"])
    doc = document_of(client, task["id"])

    r = client.post(
        f"/documents/{doc}/comments",
        json={"content": "I already handled this."},
        headers={"X-Cheese-Token": token},
    )

    assert r.status_code == 200
    comment = r.json()["data"]
    assert comment["author"] == room_agent_seat(client, room)
    assert sessions.asked == []
