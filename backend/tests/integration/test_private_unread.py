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
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)


def _project(client) -> str:
    return post_project(client, json={"name": "Demo"}).json()["data"]["id"]


def _on_the_roster(client, project_id: str, *handles: str) -> None:
    """把这些人放进项目名册。

    The badge map is a *project* read and asks the project door (``topics.py``),
    so the two seats a DM is about have to be members of it. They always were
    participants by intent — they simply did not have to be listed while the
    route only asked whose mailbox was being read.
    """
    for handle in handles:
        join_project_team(client, project_id, handle)


def _dm(client, project_id: str, user: str, peer: str | None = None) -> str:
    params: dict[str, str] = {"user_handle": user}
    if peer is not None:
        params["peer_handle"] = peer
    r = client.get(f"/projects/{project_id}/private-chat", params=params)
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _seed_message(client, project_id: str, topic_id: str, author: str) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:
            await BlockRepository(session).add(
                project_id=uuid.UUID(project_id),
                conversation_id=uuid.UUID(topic_id),
                author=author,
                author_type=AuthorType.participant,
                content="msg",
                kind=BlockKind.message,
            )
            await session.commit()

    asyncio.run(_run())


def _private_unread(client, project_id: str, handle: str) -> dict:
    # Per-person, like the topic badge map: authenticate as the handle asked for.
    r = client.get(
        f"/projects/{project_id}/private-unread",
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
        f"/projects/{project_id}/private-unread",
        params={"handle": "mentor-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 403
    # ...and with no credential at all.
    r = client.get(
        f"/projects/{project_id}/private-unread", params={"handle": "mentor-1"}
    )
    assert r.status_code == 401


def test_peer_dm_unread_is_keyed_by_the_other_party(client):
    project_id = _project(client)
    _on_the_roster(client, project_id, "user-1", "mentor-1")
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
    _on_the_roster(client, project_id, "user-1", "mentor-1")
    dm = _dm(client, project_id, "user-1", "mentor-1")
    _seed_message(client, project_id, dm, "mentor-1")
    assert _private_unread(client, project_id, "user-1") == {"mentor-1": 1}

    # Opening a DM bumps the same read cursor a topic uses.
    r = client.post(
        f"/topics/{dm}/read",
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


def test_a_teammate_dm_is_keyed_by_that_teammate(client):
    """A DM with an AI teammate is addressed `agent:<handle>`, not by the bare
    handle: teammate names are chosen per project and can collide with a
    person's (see test_private_chat_per_agent)."""
    project_id = _project(client)
    _on_the_roster(client, project_id, "user-1")
    dm = _dm(client, project_id, "user-1")  # no peer → the default teammate
    _seed_message(client, project_id, dm, "cheese")
    assert _private_unread(client, project_id, "user-1") == {"agent:cheese": 1}


def test_other_peoples_dms_are_invisible(client):
    project_id = _project(client)
    _on_the_roster(client, project_id, "user-1", "mentor-1", "mentor-2")
    theirs = _dm(client, project_id, "mentor-1", "mentor-2")
    _seed_message(client, project_id, theirs, "mentor-1")
    assert _private_unread(client, project_id, "user-1") == {}


def test_group_topics_never_appear_in_the_private_map(client):
    project_id = _project(client)
    _on_the_roster(client, project_id, "user-1", "mentor-1")
    tr = client.post("/topics", json={"project_id": project_id, "title": "房间"})
    topic_id = tr.json()["data"]["id"]
    joined = client.post(
        f"/topics/{topic_id}/join", headers=session_auth_headers("user-1")
    )
    assert joined.status_code == 200, joined.text
    _seed_message(client, project_id, topic_id, "mentor-1")

    assert _private_unread(client, project_id, "user-1") == {}
    # ...while the topic-keyed map still reports it, unchanged.
    r = client.get(
        f"/projects/{project_id}/topic-unread",
        params={"handle": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.json()["data"][topic_id]["messages"] == 1
