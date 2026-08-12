"""话题级未读角标 + 手动归档 (前端体验优化: 对标飞书的未读数 & 归档去向)."""

import asyncio

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from tests.conftest import wait_turns_idle
from tests.integration.conftest import session_auth_headers


def _create_project_and_topic(client, title: str = "话题A") -> tuple[str, str]:
    pr = client.post("/api/projects", json={"name": "Demo"})
    project_id = pr.json()["data"]["id"]
    tr = client.post("/api/topics", json={"project_id": project_id, "title": title})
    topic_id = tr.json()["data"]["id"]
    return project_id, topic_id


def _seed_message(client, project_id: str, topic_id: str, author: str) -> None:
    """Drop a message block directly (the WS chat path is covered elsewhere)."""
    import uuid

    async def _run() -> None:
        async with client.test_factory() as session:
            await BlockRepository(session).add(
                project_id=uuid.UUID(project_id),
                topic_id=uuid.UUID(topic_id),
                author=author,
                author_type=AuthorType.human,
                content="msg",
                kind=BlockKind.message,
            )
            await session.commit()

    asyncio.run(_run())


def _unread(client, project_id: str, handle: str) -> dict:
    # The badge map is per-person: authenticate as the handle being asked for.
    r = client.get(
        f"/api/projects/{project_id}/topic-unread",
        params={"handle": handle},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200
    return r.json()["data"]


def test_topic_unread_counts_and_read_cursor(client):
    project_id, topic_id = _create_project_and_topic(client)

    # No messages yet → no unread entries at all.
    assert _unread(client, project_id, "user-1") == {}

    # Two messages by others → 2 unread for user-1.
    _seed_message(client, project_id, topic_id, "cheese")
    _seed_message(client, project_id, topic_id, "mentor-1")
    assert _unread(client, project_id, "user-1").get(topic_id) == 2

    # Own messages never count as unread.
    _seed_message(client, project_id, topic_id, "user-1")
    assert _unread(client, project_id, "user-1").get(topic_id) == 2
    # ...but they do for the other side.
    assert _unread(client, project_id, "mentor-1").get(topic_id) == 2

    # Opening the topic (mark read) clears the badge for that user only.
    r = client.post(f"/api/topics/{topic_id}/read", json={"handle": "user-1"})
    assert r.status_code == 200
    assert topic_id not in _unread(client, project_id, "user-1")
    assert _unread(client, project_id, "mentor-1").get(topic_id) == 2

    # A new message after the cursor lights it up again.
    _seed_message(client, project_id, topic_id, "cheese")
    assert _unread(client, project_id, "user-1").get(topic_id) == 1


def test_mark_read_requires_handle(client):
    _, topic_id = _create_project_and_topic(client)
    r = client.post(f"/api/topics/{topic_id}/read", json={})
    assert r.status_code == 422


def test_notifications_unread_count_and_read_all(client):
    project_id, topic_id = _create_project_and_topic(client)

    def _notify(level: str, target: str | None = None) -> None:
        r = client.post(
            f"/api/projects/{project_id}/notifications",
            json={
                "level": level,
                "kind": "change_alert",
                "title": f"n-{level}",
                "target_handle": target,
            },
        )
        assert r.status_code == 200

    _notify("light")
    _notify("strong", target="user-1")
    _notify("silent")  # 默默记下来 — never lights the badge
    _notify("light", target="someone-else")  # not visible to user-1

    r = client.get(
        f"/api/projects/{project_id}/notifications/unread-count",
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"]["unread"] == 2

    # 全部标记已读 marks everything visible to user-1 (incl. the silent one).
    r = client.post(
        f"/api/projects/{project_id}/notifications/read-all",
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"]["marked"] == 3

    r = client.get(
        f"/api/projects/{project_id}/notifications/unread-count",
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"]["unread"] == 0

    # someone-else's notification is untouched.
    r = client.get(
        f"/api/projects/{project_id}/notifications/unread-count",
        headers=session_auth_headers("someone-else"),
    )
    assert r.json()["data"]["unread"] == 1


def test_manual_archive_and_unarchive(client):
    project_id, topic_id = _create_project_and_topic(client)

    r = client.post(f"/api/topics/{topic_id}/archive", json={"by": "user-1"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "archived"
    assert data["archived_at"] is not None

    # Idempotent.
    r = client.post(f"/api/topics/{topic_id}/archive", json={"by": "user-1"})
    assert r.json()["data"]["status"] == "archived"

    r = client.post(f"/api/topics/{topic_id}/unarchive", json={"by": "user-1"})
    assert r.json()["data"]["status"] == "active"
    assert r.json()["data"]["archived_at"] is None


def test_root_topic_cannot_be_archived(client):
    pr = client.post("/api/projects", json={"name": "RootGuard"})
    root_topic_id = pr.json()["data"].get("root_topic_id")
    if root_topic_id is None:
        return  # project without a root topic — nothing to guard
    r = client.post(f"/api/topics/{root_topic_id}/archive", json={"by": "user-1"})
    assert r.status_code == 422


def test_archive_cascades_to_subtopics(client):
    """归档整件事：a topic's active subtopics (and theirs) archive with it —
    orphan 分身 without their parent context are meaningless."""
    pr = client.post("/api/projects", json={"name": "P"})
    pid = pr.json()["data"]["id"]
    t = client.post("/api/topics", json={"project_id": pid, "title": "父"})
    parent = t.json()["data"]["id"]
    # Each split auto-kicks its 分身 (a background turn). On the test DB (one
    # shared SQLite connection) a running turn races the NEXT write request, so
    # let each kickoff finish before continuing (prod Postgres doesn't care).
    c1 = client.post(
        f"/api/topics/{parent}/split", json={"title": "子1", "created_by": "u"}
    ).json()["data"]["id"]
    wait_turns_idle()
    c2 = client.post(
        f"/api/topics/{parent}/split", json={"title": "子2", "created_by": "u"}
    ).json()["data"]["id"]
    wait_turns_idle()
    g = client.post(
        f"/api/topics/{c1}/split", json={"title": "孙", "created_by": "u"}
    ).json()["data"]["id"]
    wait_turns_idle()

    r = client.post(f"/api/topics/{parent}/archive", json={"by": "u"})
    assert r.status_code == 200
    for tid in (parent, c1, c2, g):
        assert (
            client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"
        ), tid
    # The cascaded child records why it went.
    blocks = client.get(f"/api/topics/{c1}/blocks").json()["data"]["data"]
    assert any("随父话题" in (b.get("content") or "") for b in blocks)
