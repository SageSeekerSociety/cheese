"""B4: inline comments anchored to a doc node."""

import asyncio
import uuid

import pytest

from app.api.deps import get_handless_sessions
from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.models import AuthorType, Block, BlockKind
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


def test_comment_anchors_to_doc_node_and_is_not_in_timeline(client):
    tid = _topic(client)
    # Build the doc tree, grab a node to anchor to.
    client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 目标\n\n做推荐系统", "expected_version": 0},
    )
    nodes = client.get(f"/topics/{tid}/docs").json()["data"]["data"]
    anchor = nodes[0]["id"]

    r = client.post(
        f"/topics/{tid}/comments",
        json={"anchor": anchor, "content": "这里要写清楚指标"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["reply_to"] == anchor
    assert r.json()["data"]["kind"] == "comment"

    comments = client.get(f"/topics/{tid}/comments").json()["data"]["data"]
    assert len(comments) == 1 and comments[0]["reply_to"] == anchor

    # A comment is a document-view block, not a conversation message.
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert not any(b["kind"] == "comment" for b in blocks)


def test_comment_stores_selected_quote(client):
    # B4 Feishu-style: a comment made on a text selection keeps the quoted span.
    tid = _topic(client)
    client.put(
        f"/topics/{tid}/doc",
        json={
            "content": "# 目标\n\n做一个课程推荐系统",
            "expected_version": 0,
        },
    )
    nodes = client.get(f"/topics/{tid}/docs").json()["data"]["data"]
    anchor = nodes[1]["id"]  # the paragraph node
    r = client.post(
        f"/topics/{tid}/comments",
        json={
            "anchor": anchor,
            "quote": "课程推荐系统",
            "content": "这个范围要再收窄",
        },
    )
    assert r.status_code == 200
    assert r.json()["data"]["anchor_quote"] == "课程推荐系统"
    assert r.json()["data"]["reply_to"] == anchor

    got = client.get(f"/topics/{tid}/comments").json()["data"]["data"]
    assert got[0]["anchor_quote"] == "课程推荐系统"


def test_comment_requires_content(client):
    tid = _topic(client)
    r = client.post(f"/topics/{tid}/comments", json={"content": "  "})
    assert r.status_code == 422


def test_comment_rejects_foreign_anchor(client):
    tid = _topic(client)
    other = _topic(client)
    client.put(
        f"/topics/{other}/doc",
        json={"content": "# X", "expected_version": 0},
    )
    foreign = client.get(f"/topics/{other}/docs").json()["data"]["data"][0]["id"]
    r = client.post(f"/topics/{tid}/comments", json={"anchor": foreign, "content": "x"})
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
        json={"anchor": nodes[1]["id"], "quote": "这一段", "content": "请改写这一段"},
        headers=headers,
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["author"] == "commenter"
    assert sessions.asked == []
    assert client.get(f"/topics/{tid}/doc").json()["data"] == before
    assert client.get(f"/topics/{tid}/docs").json()["data"]["data"] == nodes
    assert client.get(f"/topics/{tid}/blocks").json()["data"]["data"] == timeline
    assert len(client.get(f"/topics/{tid}/comments").json()["data"]["data"]) == 1


@pytest.mark.parametrize(
    "target", ["root", "message", "orphan", "wrong-root", "deleted", "malformed"]
)
def test_comment_rejects_non_document_node_anchor(client, target):
    tid = _topic(client)
    client.put(
        f"/topics/{tid}/doc",
        json={"content": "# X", "expected_version": 0},
    )
    if target == "root":
        anchor = client.get(f"/topics/{tid}/doc").json()["data"]["id"]
    elif target in {"message", "orphan", "wrong-root"}:
        topic = client.get(f"/topics/{tid}").json()["data"]

        async def seed():
            async with client.test_factory() as session:
                parent = None
                if target == "wrong-root":
                    parent = Block(
                        project_id=uuid.UUID(topic["project_id"]),
                        topic_id=uuid.UUID(tid),
                        kind=BlockKind.doc,
                        content="noncanonical document",
                        author_type=AuthorType.participant,
                        author="u",
                    )
                    session.add(parent)
                    await session.flush()
                node = Block(
                    project_id=uuid.UUID(topic["project_id"]),
                    topic_id=uuid.UUID(tid),
                    kind=BlockKind.message
                    if target == "message"
                    else BlockKind.doc_node,
                    content="not in the current document",
                    author_type=AuthorType.participant,
                    author="u",
                    struct_parent=parent.id if parent else None,
                )
                session.add(node)
                await session.flush()
                result = str(node.id)
                await session.commit()
                return result

        anchor = asyncio.run(seed())
    elif target == "deleted":
        anchor = client.get(f"/topics/{tid}/docs").json()["data"]["data"][0]["id"]
        updated = client.put(
            f"/topics/{tid}/doc",
            json={"content": "replacement", "expected_version": 1},
        )
        assert updated.status_code == 200, updated.text
    else:
        anchor = "not-a-uuid"
    response = client.post(
        f"/topics/{tid}/comments", json={"anchor": anchor, "content": "comment"}
    )
    assert response.status_code == 422, response.text
    assert client.get(f"/topics/{tid}/comments").json()["data"]["data"] == []


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
