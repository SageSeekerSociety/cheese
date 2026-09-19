"""B4: inline comments anchored to a doc node."""

import pytest

from app.api.deps import get_work_runner
from app.core.sandbox_auth import mint_scoped_token
from app.main import app
from tests.integration.conftest import room_agent_seat


class _RecordingRunner:
    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def submit(self, chat_service, topic_id, **kw):
        self.submitted.append({"topic_id": topic_id, **kw})

    def running_topic_ids(self):
        return set()


@pytest.fixture
def runner():
    fake = _RecordingRunner()
    app.dependency_overrides[get_work_runner] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_work_runner, None)


def _topic(client) -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return t["id"]


def test_comment_anchors_to_doc_node_and_is_not_in_timeline(client):
    tid = _topic(client)
    # Build the doc tree, grab a node to anchor to.
    client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 目标\n\n做推荐系统", "author": "u", "expected_version": 0},
    )
    nodes = client.get(f"/topics/{tid}/docs").json()["data"]["data"]
    anchor = nodes[0]["id"]

    r = client.post(
        f"/topics/{tid}/comments",
        json={"anchor": anchor, "content": "这里要写清楚指标", "author": "user-1"},
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
            "author": "u",
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
            "author": "user-1",
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
        json={"content": "# X", "author": "u", "expected_version": 0},
    )
    foreign = client.get(f"/topics/{other}/docs").json()["data"]["data"][0]["id"]
    r = client.post(f"/topics/{tid}/comments", json={"anchor": foreign, "content": "x"})
    assert r.status_code == 422


def test_agent_comment_is_attributed_but_does_not_wake_itself(client, runner):
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
    assert comment["author_type"] == "ai"
    assert runner.submitted == []
