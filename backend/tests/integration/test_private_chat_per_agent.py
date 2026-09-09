"""一人一间：私聊按 AI 队友分开，而不是每个项目一间。

A project can have several AI teammates, each with its own role, model and
memory pool. Before this, a member had exactly ONE private chat in a project and
whoever happened to be the project's default answered it — so picking a
specialist to talk to privately was not expressible, and changing the default
silently changed who you had been talking to.

These tests pin the two halves of the fix: a room per (member, teammate) pair,
and a room that stays with its teammate when the project's default moves on.
"""

import asyncio
import uuid

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.topic.repositories import TopicRepository
from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    return client.post("/projects", json={"name": "Demo"}).json()["data"]["id"]


def _agents(client, project_id: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/agents")
    assert r.status_code == 200
    return r.json()["data"]["data"]


def _add_agent(client, project_id: str, handle: str, name: str) -> dict:
    r = client.post(
        f"/projects/{project_id}/agents",
        json={"handle": handle, "display_name": name},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _make_default(client, project_id: str, instance_id: str) -> None:
    r = client.put(
        f"/projects/{project_id}/default-agent", json={"instance_id": instance_id}
    )
    assert r.status_code == 200, r.text


def _dm(client, project_id: str, user: str, agent: str | None = None):
    params: dict[str, str] = {"user_handle": user}
    if agent is not None:
        params["agent_handle"] = agent
    return client.get(f"/projects/{project_id}/private-chat", params=params)


def _dm_id(client, project_id: str, user: str, agent: str | None = None) -> str:
    r = _dm(client, project_id, user, agent)
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _who_answers(client, topic_id: str) -> dict:
    r = client.get(f"/topics/{topic_id}/agent")
    assert r.status_code == 200, r.text
    return r.json()["data"]


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
    r = client.get(
        f"/projects/{project_id}/private-unread",
        params={"handle": handle},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200
    return r.json()["data"]


def test_each_teammate_gets_its_own_room_and_keeps_it(client):
    project_id = _project(client)
    default = next(a for a in _agents(client, project_id) if a["is_default"])
    reviewer = _add_agent(client, project_id, "reviewer", "评审")

    with_default = _dm_id(client, project_id, "user-1", default["handle"])
    with_reviewer = _dm_id(client, project_id, "user-1", "reviewer")
    assert with_default != with_reviewer

    # Asking again lands in the same conversation rather than opening a new one.
    assert _dm_id(client, project_id, "user-1", "reviewer") == with_reviewer
    assert _dm_id(client, project_id, "user-1", default["handle"]) == with_default

    # And each room is answered by the teammate it belongs to — the payoff.
    assert _who_answers(client, with_reviewer)["handle"] == "reviewer"
    assert _who_answers(client, with_reviewer)["display_name"] == "评审"
    assert _who_answers(client, with_default)["handle"] == default["handle"]

    # Different people do not share a room with the same teammate.
    assert _dm_id(client, project_id, "user-2", "reviewer") != with_reviewer


def test_naming_no_teammate_opens_the_projects_default(client):
    project_id = _project(client)
    default = next(a for a in _agents(client, project_id) if a["is_default"])
    assert _dm_id(client, project_id, "user-1") == _dm_id(
        client, project_id, "user-1", default["handle"]
    )


def test_a_room_stays_with_its_teammate_when_the_default_moves(client):
    """换默认队友不搬走已有的对话 —— this is the whole reason rooms name a
    teammate instead of following the project."""
    project_id = _project(client)
    first = next(a for a in _agents(client, project_id) if a["is_default"])
    room = _dm_id(client, project_id, "user-1", first["handle"])

    reviewer = _add_agent(client, project_id, "reviewer", "评审")
    _make_default(client, project_id, reviewer["id"])

    # The old conversation is still the old teammate's...
    assert _who_answers(client, room)["handle"] == first["handle"]
    assert _dm_id(client, project_id, "user-1", first["handle"]) == room
    # ...and the new default is a new room, not a takeover of that one.
    assert _dm_id(client, project_id, "user-1") != room


def test_a_dm_from_before_teammates_were_named_keeps_its_history(client):
    """A room opened before this feature names no teammate and is answered by
    whatever the default is. Opening it must adopt it, not leave the history
    behind in an orphan room nobody can reach."""
    project_id = _project(client)
    default = next(a for a in _agents(client, project_id) if a["is_default"])

    async def _legacy_room() -> str:
        async with client.test_factory() as session:
            topic = await TopicRepository(session).get_or_create_private(
                project_id=uuid.UUID(project_id), user_handle="user-1"
            )
            await session.commit()
            return str(topic.id)

    legacy = asyncio.run(_legacy_room())
    _seed_message(client, project_id, legacy, "cheese")

    assert _dm_id(client, project_id, "user-1", default["handle"]) == legacy
    # Adopted, so it no longer moves with the project's default.
    reviewer = _add_agent(client, project_id, "reviewer", "评审")
    _make_default(client, project_id, reviewer["id"])
    assert _who_answers(client, legacy)["handle"] == default["handle"]


def test_unread_is_counted_per_teammate(client):
    project_id = _project(client)
    default = next(a for a in _agents(client, project_id) if a["is_default"])
    _add_agent(client, project_id, "reviewer", "评审")

    with_default = _dm_id(client, project_id, "user-1", default["handle"])
    with_reviewer = _dm_id(client, project_id, "user-1", "reviewer")
    _seed_message(client, project_id, with_default, "cheese")
    _seed_message(client, project_id, with_reviewer, "cheese")
    _seed_message(client, project_id, with_reviewer, "cheese")

    assert _private_unread(client, project_id, "user-1") == {
        f"agent:{default['handle']}": 1,
        "agent:reviewer": 2,
    }


def test_a_teammate_badge_cannot_be_confused_with_a_persons(client):
    """Teammate handles are chosen per project, so one can be named after a
    person on the roster. The two DMs must still be told apart."""
    project_id = _project(client)
    _add_agent(client, project_id, "mentor-1", "同名队友")

    with_person = client.get(
        f"/projects/{project_id}/private-chat",
        params={"user_handle": "user-1", "peer_handle": "mentor-1"},
    )
    assert with_person.status_code == 200
    person_room = with_person.json()["data"]["id"]
    agent_room = _dm_id(client, project_id, "user-1", "mentor-1")
    assert person_room != agent_room

    _seed_message(client, project_id, person_room, "mentor-1")
    _seed_message(client, project_id, agent_room, "cheese")
    assert _private_unread(client, project_id, "user-1") == {
        "mentor-1": 1,
        "agent:mentor-1": 1,
    }


def test_talking_to_a_teammate_that_is_not_here_says_so(client):
    project_id = _project(client)
    r = _dm(client, project_id, "user-1", "nobody")
    assert r.status_code == 404
    assert "nobody" in r.json()["message"]
