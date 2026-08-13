"""私聊未读角标 — the DM half of 话题级未读.

Private chats are not in the topic tree, so the sidebar renders one row per
project member from the roster and never learns a conversation's topic id.
``/topic-unread`` is keyed by topic id and is therefore unusable there: a DM
could hold unread messages and its row looked identical to an empty one.
These tests pin the peer-keyed map that fixes it.
"""

import asyncio
import uuid

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    return client.post("/api/projects", json={"name": "Demo"}).json()["data"]["id"]


def _dm(client, project_id: str, user: str, peer: str | None = None) -> str:
    params: dict[str, str] = {"user_handle": user}
    if peer is not None:
        params["peer_handle"] = peer
    r = client.get(f"/api/projects/{project_id}/private-chat", params=params)
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _seed_message(client, project_id: str, topic_id: str, author: str) -> None:
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


def _private_unread(client, project_id: str, handle: str) -> dict:
    # Per-person, like the topic badge map: authenticate as the handle asked for.
    r = client.get(
        f"/api/projects/{project_id}/private-unread",
        params={"handle": handle},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200
    return r.json()["data"]


def test_one_person_cannot_read_anothers_dm_badges(client):
    """Who you are DMing is private — naming someone else must not read it."""
    project_id = _project(client)
    dm = _dm(client, project_id, "mentor-1", "mentor-2")
    _seed_message(client, project_id, dm, "mentor-2")

    r = client.get(
        f"/api/projects/{project_id}/private-unread",
        params={"handle": "mentor-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 403
    # ...and with no credential at all.
    r = client.get(
        f"/api/projects/{project_id}/private-unread", params={"handle": "mentor-1"}
    )
    assert r.status_code == 401


def test_peer_dm_unread_is_keyed_by_the_other_party(client):
    project_id = _project(client)
    dm = _dm(client, project_id, "user-1", "mentor-1")

    # Nothing said yet.
    assert _private_unread(client, project_id, "user-1") == {}

    # What the peer says is unread for me, keyed by THEIR handle...
    _seed_message(client, project_id, dm, "mentor-1")
    _seed_message(client, project_id, dm, "mentor-1")
    assert _private_unread(client, project_id, "user-1") == {"mentor-1": 2}
    # ...and the same row is keyed by MY handle when they look at it.
    assert _private_unread(client, project_id, "mentor-1") == {}

    # My own messages never count against me, but do for them.
    _seed_message(client, project_id, dm, "user-1")
    assert _private_unread(client, project_id, "user-1") == {"mentor-1": 2}
    assert _private_unread(client, project_id, "mentor-1") == {"user-1": 1}


def test_opening_the_dm_clears_it_and_new_messages_light_it_again(client):
    project_id = _project(client)
    dm = _dm(client, project_id, "user-1", "mentor-1")
    _seed_message(client, project_id, dm, "mentor-1")
    assert _private_unread(client, project_id, "user-1") == {"mentor-1": 1}

    # Opening a DM bumps the same read cursor a topic uses.
    r = client.post(
        f"/api/topics/{dm}/read",
        json={"handle": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 200
    assert _private_unread(client, project_id, "user-1") == {}
    # The peer's own badge is untouched by my reading.
    _seed_message(client, project_id, dm, "user-1")
    assert _private_unread(client, project_id, "mentor-1") == {"user-1": 1}

    # A message after the cursor lights it up again.
    _seed_message(client, project_id, dm, "mentor-1")
    assert _private_unread(client, project_id, "user-1") == {"mentor-1": 1}


def test_cheese_dm_is_keyed_by_the_cheese_handle(client):
    project_id = _project(client)
    dm = _dm(client, project_id, "user-1")  # no peer → the 芝士 DM
    _seed_message(client, project_id, dm, "cheese")
    assert _private_unread(client, project_id, "user-1") == {"cheese": 1}


def test_other_peoples_dms_are_invisible(client):
    project_id = _project(client)
    theirs = _dm(client, project_id, "mentor-1", "mentor-2")
    _seed_message(client, project_id, theirs, "mentor-1")
    assert _private_unread(client, project_id, "user-1") == {}


def test_group_topics_never_appear_in_the_private_map(client):
    project_id = _project(client)
    tr = client.post("/api/topics", json={"project_id": project_id, "title": "房间"})
    topic_id = tr.json()["data"]["id"]
    _seed_message(client, project_id, topic_id, "mentor-1")

    assert _private_unread(client, project_id, "user-1") == {}
    # ...while the topic-keyed map still reports it, unchanged.
    r = client.get(
        f"/api/projects/{project_id}/topic-unread",
        params={"handle": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"].get(topic_id) == 1
