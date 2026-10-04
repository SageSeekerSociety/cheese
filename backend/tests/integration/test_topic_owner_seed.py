"""POST /topics — a newborn room must never be ownerless (话题拥有者可能为空).

`seed()` deliberately refuses to make 芝士 the owner, and `create()` used to
pass `created_by` straight through: a topic created BY the agent, or by a caller
whose token didn't resolve, was born with 芝士 as its only member and NO owner —
so nobody could manage its roster (`can_manage_roster` needs owner/admin). On
the dogfood project this had reached 96 of 149 topics.

These tests pin the fallback ladder — real creator → parent room's owner →
project owner → the owner of the project's team — and the escape hatch that
gets the ALREADY-broken rooms out: while a room has no manager at all, whoever
manages the project (its owner, or a team owner/admin) may appoint one.
Without it those rooms are a dead end with no route out of the product (only an
owner may appoint an owner, and there is none), repairable only by hand-editing
the database.
"""

import asyncio
import uuid

from sqlalchemy import delete, select

from app.core.sandbox_auth import mint_scoped_token
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import Topic, TopicMembership, TopicRole
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)


def _project(client, owner: str | None) -> dict:
    return post_project(client, json={"name": "P"}, owner=owner).json()["data"]


def _roster(client, topic_id: str) -> dict[str, str]:
    rows = client.get(f"/topics/{topic_id}/members").json()["data"]["data"]
    return {m["member_handle"]: m["role"] for m in rows}


def _agent_roles(client, topic_id: str) -> list[str]:
    """The roles of the agents seated in the room."""
    rows = client.get(f"/topics/{topic_id}/members").json()["data"]["data"]
    return [m["role"] for m in rows if m["agent"]]


def _create_topic(client, project_id: str, **kw) -> dict:
    body = {"project_id": project_id, "title": "T", **kw.pop("json", {})}
    return client.post("/topics", json=body, **kw).json()["data"]


def _add_team_member(client, project_id: str, handle: str, *, admin: bool) -> None:
    """Put ``handle`` on the project's team — an admin answers for the project,
    a plain member only belongs to it."""
    join_project_team(client, project_id, handle, admin=admin)


def _orphan_the_roster(client, topic_id: str) -> None:
    """Reproduce the legacy shape this hatch exists for: a roster with no owner.

    Topic-create can't produce one any more (that's the fix above), so the only
    honest way to test the rescue path is to build the broken state directly —
    the same state 96 live topics are sitting in.
    """

    async def _go() -> None:
        async with client.test_factory() as session:
            await session.execute(
                delete(TopicMembership).where(
                    TopicMembership.topic_id == uuid.UUID(topic_id),
                    TopicMembership.role == TopicRole.owner,
                )
            )
            await session.commit()

    asyncio.run(_go())


def _make_project_ownerless(client, project_id: str) -> None:
    """Reproduce the live shape: `owner_handle` NULL **and** a root room with no
    owner either.

    Both halves are load-bearing. Creating a project over HTTP anonymously does
    not produce it — the caller resolves to the handle ``anonymous``, so the
    project is owned by *that* and its root topic inherits the same owner. Strip
    only the project column and the ladder still stops one rung early, on the
    root room's ``anonymous`` owner, and never reaches the roster. The dogfood
    project predates that attribution and has neither, which is exactly why its
    rooms came out blank.
    """

    async def _go() -> None:
        async with client.test_factory() as session:
            project = await ProjectRepository(session).get(uuid.UUID(project_id))
            assert project is not None
            project.owner_handle = None
            await session.execute(
                delete(TopicMembership).where(
                    TopicMembership.role == TopicRole.owner,
                    TopicMembership.topic_id.in_(
                        select(Topic.id).where(
                            Topic.project_id == uuid.UUID(project_id)
                        )
                    ),
                )
            )
            await session.commit()

    asyncio.run(_go())


def _strip_team_owner(client, project_id: str) -> None:
    """End the owner relation of the project's team, so no rung is left."""
    from datetime import UTC, datetime

    from sqlalchemy import update

    from app.domain.team.models import TeamMemberRole, TeamUserRelation

    async def _go() -> None:
        async with client.test_factory() as session:
            project = await ProjectRepository(session).get(uuid.UUID(project_id))
            assert project is not None
            await session.execute(
                update(TeamUserRelation)
                .where(
                    TeamUserRelation.team_id == project.team_id,
                    TeamUserRelation.role == TeamMemberRole.OWNER,
                )
                .values(deleted_at=datetime.now(UTC))
            )
            await session.commit()

    asyncio.run(_go())


def test_topic_created_by_human_is_owned_by_that_human(client):
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=session_auth_headers("alice"))

    assert _roster(client, topic["id"]).get("alice") == "owner"


def test_topic_created_by_cheese_falls_back_to_project_owner(client):
    """The bug users hit: 芝士 creating a room left it with no owner at all.
    The call here names no person either, which is what that came down to."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"])

    roster = _roster(client, topic["id"])
    assert roster.get("alice") == "owner"
    # The room seats the project's default agent as a member — not the shared
    # platform ``cheese`` account, which no longer sits in rooms.
    assert _agent_roles(client, topic["id"]) == ["member"]


def test_an_ownerless_project_falls_back_to_its_teams_owner(client):
    """The rung below "the project's owner", and the one the dogfood project
    actually needed.

    Measured there on 2026-08-12, answering 「新话题的拥有者为什么有的有，有的是
    空的」: the project's own `owner_handle` is NULL and its root topic is
    ownerless too, so a topic 芝士 opens under the root falls through creator
    (an agent handle — skipped), parent owner (empty), and project owner (NULL),
    and is born blank. Five active rooms were sitting in that state, none of
    them manageable by anyone.

    The ladder was right; its bottom rung had nothing to stand on. The team
    does — its owner is "who answers for this project" already recorded rather
    than a policy invented to fill the hole.
    """
    p = _project(client, owner="dana")
    _make_project_ownerless(client, p["id"])

    topic = _create_topic(client, p["id"])

    # dana owns the personal team the project was created in.
    assert _roster(client, topic["id"]).get("dana") == "owner"


def test_a_room_with_nobody_to_inherit_from_is_still_created(client):
    """No creator, no parent owner, no project owner, no team owner — the ladder runs
    out. It must not raise: an ownerless room is a roster problem the rescue
    hatch below already covers, but a 500 on topic-create is a dead product.
    """
    p = _project(client, owner=None)
    _make_project_ownerless(client, p["id"])
    _strip_team_owner(client, p["id"])
    topic = _create_topic(client, p["id"])

    roster = _roster(client, topic["id"])
    assert "owner" not in roster.values()
    assert _agent_roles(client, topic["id"]) == ["member"]


def _insert_block(client, project_id: str, topic_id: str, content: str) -> str:
    """A message in the room, written straight to the DB — posting it through the
    API would kick a turn off and race the upgrade we are actually testing."""
    holder: dict[str, str] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:
            block = Block(
                project_id=uuid.UUID(project_id),
                topic_id=uuid.UUID(topic_id),
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="alice",
                content=content,
                refs=[],
            )
            session.add(block)
            await session.flush()
            holder["id"] = str(block.id)
            await session.commit()

    asyncio.run(_seed())
    return holder["id"]


def test_an_ai_teammate_cannot_turn_a_message_into_a_task(client):
    """Only a person turns a message into a task and owns it; the room's agent
    proposes one instead."""
    p = _project(client, owner="alice")
    room = _create_topic(client, p["id"], headers=session_auth_headers("alice"))
    block_id = _insert_block(client, p["id"], room["id"], "这块值得单独开一个话题")
    token = mint_scoped_token(project_id=p["id"], topic_id=room["id"])

    refused = client.post(
        f"/blocks/{block_id}/upgrade", headers={"X-Cheese-Token": token}
    )

    assert refused.status_code == 403
    assert client.get(f"/topics/{room['id']}/tasks").json()["data"]["data"] == []


def test_owner_can_manage_roster_of_an_agent_created_topic(client):
    """The functional consequence of the fix: with an owner seeded, a human can
    actually add members to a room 芝士 opened. Before, this was a 403 with no
    way out — no owner existed to grant anyone anything."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"])
    join_project_team(client, p["id"], "bob")

    r = client.post(
        f"/topics/{topic['id']}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert _roster(client, topic["id"]).get("bob") == "member"


def test_a_team_admin_can_rescue_a_room_that_lost_its_owner(client):
    """The way out for the 96 rooms already stuck: a team admin may appoint an
    owner while the room has none. Before this, the only fix was a DB script."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=session_auth_headers("alice"))
    _orphan_the_roster(client, topic["id"])
    assert "owner" not in _roster(client, topic["id"]).values()

    _add_team_member(client, p["id"], "dana", admin=True)
    r = client.post(
        f"/topics/{topic['id']}/members",
        json={"handle": "dana", "role": "owner", "actor": "dana"},
        headers=session_auth_headers("dana"),
    )
    assert r.status_code == 200
    assert _roster(client, topic["id"]).get("dana") == "owner"


def test_plain_project_member_cannot_rescue_a_room(client):
    """The hatch is for whoever answers for the project, not for everyone in it."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=session_auth_headers("alice"))
    _orphan_the_roster(client, topic["id"])

    _add_team_member(client, p["id"], "erin", admin=False)
    r = client.post(
        f"/topics/{topic['id']}/members",
        json={"handle": "erin", "role": "owner", "actor": "erin"},
        headers=session_auth_headers("erin"),
    )
    assert r.status_code == 403


def test_hatch_closes_once_the_room_has_an_owner_again(client):
    """A healthy room's owner is never overridden — the admin loses the power the
    moment the room can manage itself, so this isn't a blanket project-wide key."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=session_auth_headers("alice"))
    _add_team_member(client, p["id"], "dana", admin=True)

    r = client.post(
        f"/topics/{topic['id']}/members",
        json={"handle": "mallory", "role": "member", "actor": "dana"},
        headers=session_auth_headers("dana"),
    )
    assert r.status_code == 403
