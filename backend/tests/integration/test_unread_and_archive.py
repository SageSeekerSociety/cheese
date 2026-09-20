"""话题级未读角标 + 手动归档 (前端体验优化: 对标飞书的未读数 & 归档去向)."""

import asyncio

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from tests.conftest import wait_work_idle
from tests.integration.conftest import session_auth_headers


def _create_project_and_topic(client, title: str = "话题A") -> tuple[str, str]:
    pr = client.post("/projects", json={"name": "Demo", "owner_handle": "user-1"})
    project_id = pr.json()["data"]["id"]
    tr = client.post(
        "/topics",
        json={"project_id": project_id, "title": title, "created_by": "user-1"},
    )
    topic_id = tr.json()["data"]["id"]
    return project_id, topic_id


def _add_member(client, project_id: str, handle: str) -> None:
    """Make `handle` a project member directly — the topic-route guards
    (2026-08-16) deny read/act to logged-in outsiders, and these tests'
    handles are participants by intent."""
    import uuid as _uuid

    from app.domain.project.models import ProjectMember

    async def _run() -> None:
        async with client.test_factory() as session:
            session.add(
                ProjectMember(project_id=_uuid.UUID(project_id), user_handle=handle)
            )
            await session.commit()

    asyncio.run(_run())


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
        f"/projects/{project_id}/topic-unread",
        params={"handle": handle},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200
    return r.json()["data"]


def test_topic_unread_counts_and_read_cursor(client):
    project_id, topic_id = _create_project_and_topic(client)
    for h in ("user-1", "mentor-1"):
        _add_member(client, project_id, h)

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

    # Opening the topic (mark read) clears the badge for that user only. The
    # cursor belongs to the verified caller; the body handle is just an assertion.
    r = client.post(
        f"/topics/{topic_id}/read",
        json={"handle": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 200
    assert topic_id not in _unread(client, project_id, "user-1")
    assert _unread(client, project_id, "mentor-1").get(topic_id) == 2

    # A new message after the cursor lights it up again.
    _seed_message(client, project_id, topic_id, "cheese")
    assert _unread(client, project_id, "user-1").get(topic_id) == 1


def test_mark_read_requires_a_verified_caller(client):
    # The handle used to be required in the body BECAUSE it was the identity;
    # now identity comes from the credential, so the missing piece is a login.
    project_id, topic_id = _create_project_and_topic(client)
    _add_member(client, project_id, "user-1")
    r = client.post(f"/topics/{topic_id}/read", json={})
    assert r.status_code == 401
    # A signed-in caller needs no body handle at all — the cursor is theirs.
    r = client.post(
        f"/topics/{topic_id}/read",
        json={},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["handle"] == "user-1"


def test_notifications_unread_count_and_read_all(client):
    project_id, topic_id = _create_project_and_topic(client)

    def _notify(level: str, target: str | None = None) -> None:
        r = client.post(
            f"/projects/{project_id}/alerts",
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
        f"/projects/{project_id}/alerts/unread-count",
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"]["unread"] == 2

    # 全部标记已读 marks everything visible to user-1 (incl. the silent one).
    r = client.post(
        f"/projects/{project_id}/alerts/read-all",
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"]["marked"] == 3

    r = client.get(
        f"/projects/{project_id}/alerts/unread-count",
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"]["unread"] == 0

    # someone-else's notification is untouched.
    r = client.get(
        f"/projects/{project_id}/alerts/unread-count",
        headers=session_auth_headers("someone-else"),
    )
    assert r.json()["data"]["unread"] == 1


def test_manual_archive_and_unarchive(client):
    project_id, topic_id = _create_project_and_topic(client)

    r = client.post(
        f"/topics/{topic_id}/archive",
        json={"by": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["status"] == "archived"
    assert data["archived_at"] is not None

    # Idempotent.
    r = client.post(
        f"/topics/{topic_id}/archive",
        json={"by": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"]["status"] == "archived"

    r = client.post(
        f"/topics/{topic_id}/unarchive",
        json={"by": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"]["status"] == "active"
    assert r.json()["data"]["archived_at"] is None


def test_root_topic_cannot_be_archived(client):
    pr = client.post("/projects", json={"name": "RootGuard", "owner_handle": "user-1"})
    root_topic_id = pr.json()["data"].get("root_topic_id")
    if root_topic_id is None:
        return  # project without a root topic — nothing to guard
    r = client.post(
        f"/topics/{root_topic_id}/archive",
        json={"by": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 422


def test_archive_cascades_to_the_work_in_the_room(client):
    """归档整件事：putting a room away closes the work still open inside it —
    a thread left running with its room gone has nobody left to report to.

    Room and thread end in different words on purpose: a room is `archived`
    (a person put it away) and a thread is `closed` (its work stopped).
    """
    pr = client.post("/projects", json={"name": "P", "owner_handle": "u"})
    pid = pr.json()["data"]["id"]
    t = client.post(
        "/topics", json={"project_id": pid, "title": "父", "created_by": "u"}
    )
    parent = t.json()["data"]["id"]
    c1 = client.post(
        f"/topics/{parent}/split",
        json=dict(reviewer_handle="alice", **{"title": "子1", "created_by": "u"}),
    ).json()["data"]["id"]
    wait_work_idle()
    c2 = client.post(
        f"/topics/{parent}/split",
        json=dict(reviewer_handle="alice", **{"title": "子2", "created_by": "u"}),
    ).json()["data"]["id"]
    wait_work_idle()

    r = client.post(
        f"/topics/{parent}/archive", json={"by": "u"}, headers=session_auth_headers("u")
    )
    assert r.status_code == 200
    assert client.get(f"/topics/{parent}").json()["data"]["status"] == "archived"

    cards = {
        c["id"]: c for c in client.get(f"/topics/{parent}/tasks").json()["data"]["data"]
    }
    for tid in (c1, c2):
        assert cards[tid]["status"] == "closed", tid
    # The cascaded card records why it went — on its own timeline, so whoever
    # opens it later sees why the work stopped mid-sentence.
    assert any("随父话题" in (b.get("content") or "") for b in cards[c1]["blocks"])
