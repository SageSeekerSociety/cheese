"""POST /topics — a newborn room must never be ownerless (话题拥有者可能为空).

`seed()` deliberately refuses to make 芝士 the owner, and `create()` used to
pass `created_by` straight through: a topic created BY the agent, or by a caller
whose token didn't resolve, was born with 芝士 as its only member and NO owner —
so nobody could manage its roster (`can_manage_roster` needs owner/admin). Worse,
`split_to_subtopic` defaults a child's owner to the PARENT's owner, so one
ownerless room made every sub-topic under it ownerless too. On the dogfood
project this had reached 96 of 149 topics.

These tests pin the fallback ladder — real creator → parent room's owner →
project owner → project 组长 — and the escape hatch that gets the ALREADY-broken
rooms out: while a room has no manager at all, the project's owner/lead may
appoint one.
Without it those rooms are a dead end with no route out of the product (only an
owner may appoint an owner, and there is none), repairable only by hand-editing
the database.
"""

import asyncio
import uuid

from sqlalchemy import delete, select

from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.identity.handles import topic_agent_handle
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import ProjectRole
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.models import Topic, TopicMembership, TopicRole
from tests.conftest import wait_work_idle as _wait_work_idle
from tests.integration.conftest import session_auth_headers


def _project(client, owner: str | None) -> dict:
    body: dict = {"name": "P"}
    if owner is not None:
        body["owner_handle"] = owner
    return client.post("/projects", json=body).json()["data"]


def _roster(client, topic_id: str) -> dict[str, str]:
    rows = client.get(f"/topics/{topic_id}/members").json()["data"]["data"]
    return {m["member_handle"]: m["role"] for m in rows}


def _create_topic(client, project_id: str, **kw) -> dict:
    body = {"project_id": project_id, "title": "T", **kw.pop("json", {})}
    return client.post("/topics", json=body, **kw).json()["data"]


def _add_project_member(
    client, project_id: str, handle: str, role: str, *, actor: str = "alice"
) -> None:
    """Seed the project roster as its owner — writing it needs an owner/lead token
    now, so an anonymous POST here would silently 403 and leave the roster empty."""
    r = client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": handle, "role": role},
        headers=session_auth_headers(actor),
    )
    assert r.status_code == 200, r.text


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


def _seed_project_lead_directly(client, project_id: str, handle: str) -> None:
    """Put a 组长 on the project roster without going through the API.

    Writing that roster needs an owner/lead token, and the whole premise here is
    a project that has neither — so an HTTP POST would 403 and leave the roster
    empty, quietly turning the test below into a test of nothing.
    """

    async def _go() -> None:
        async with client.test_factory() as session:
            await MemberRepository(session).add(
                project_id=uuid.UUID(project_id),
                user_handle=handle,
                role=ProjectRole.lead,
            )
            await session.commit()

    asyncio.run(_go())


def test_topic_created_by_human_is_owned_by_that_human(client):
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=session_auth_headers("alice"))

    assert _roster(client, topic["id"]).get("alice") == "owner"


def test_topic_created_by_cheese_falls_back_to_project_owner(client):
    """The bug users hit: 芝士 creating a room left it with no owner at all."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], json={"created_by": "cheese"})

    roster = _roster(client, topic["id"])
    assert roster.get("alice") == "owner"
    # The room's agent seat is THIS topic's 分身 (``cheese-<topic hex>``), not the
    # shared platform ``cheese`` account — that account no longer sits in rooms.
    assert roster.get(topic_agent_handle(uuid.UUID(topic["id"]))) == "member"


def test_topic_created_anonymously_still_gets_an_owner(client):
    """A token-less Phase-0 call (no `created_by` at all) — the shape the web UI
    degrades to when its session token is missing or expired."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"])

    assert _roster(client, topic["id"]).get("alice") == "owner"


def test_an_ownerless_project_falls_back_to_its_lead(client):
    """The rung below "the project's owner", and the one the dogfood project
    actually needed.

    Measured there on 2026-08-12, answering 「新话题的拥有者为什么有的有，有的是
    空的」: the project's own `owner_handle` is NULL and its root topic is
    ownerless too, so a topic 芝士 opens under the root falls through creator
    (an agent handle — skipped), parent owner (empty), and project owner (NULL),
    and is born blank. Five active rooms were sitting in that state, none of
    them manageable by anyone.

    The ladder was right; its bottom rung had nothing to stand on. The roster
    did — that project has a 组长, which is "who answers for this project"
    already recorded rather than a policy invented to fill the hole.
    """
    p = _project(client, owner=None)
    _make_project_ownerless(client, p["id"])
    _seed_project_lead_directly(client, p["id"], "dana")

    topic = _create_topic(client, p["id"], json={"created_by": "cheese"})

    assert _roster(client, topic["id"]).get("dana") == "owner"


def test_a_room_with_nobody_to_inherit_from_is_still_created(client):
    """No creator, no parent owner, no project owner, no lead — the ladder runs
    out. It must not raise: an ownerless room is a roster problem the rescue
    hatch below already covers, but a 500 on topic-create is a dead product.
    """
    p = _project(client, owner=None)
    _make_project_ownerless(client, p["id"])
    topic = _create_topic(client, p["id"], json={"created_by": "cheese"})

    roster = _roster(client, topic["id"])
    assert "owner" not in roster.values()
    assert roster.get(topic_agent_handle(uuid.UUID(topic["id"]))) == "member"


def test_subtopic_under_agent_created_room_is_not_ownerless(client):
    """The cascade: an ownerless room used to make every sub-topic under it
    ownerless too, because the child's owner defaults to the parent's."""
    p = _project(client, owner="alice")
    room = _create_topic(client, p["id"], json={"created_by": "cheese"})

    child = client.post(
        f"/topics/{room['id']}/split",
        json={"title": "分身拆出的子任务", "created_by": "cheese"},
    ).json()["data"]

    assert _roster(client, child["id"]).get("alice") == "owner"


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
                author_type=AuthorType.human,
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


def test_upgraded_block_falls_back_to_project_owner(client):
    """讨论升级 is normally the 分身's own suggestion, so `created_by` is an agent
    handle — which seed() drops. Without the ladder the upgraded room was born
    ownerless, the last path still producing them after create()/split were fixed.
    """
    p = _project(client, owner="alice")
    room = _create_topic(client, p["id"], headers=session_auth_headers("alice"))
    block_id = _insert_block(client, p["id"], room["id"], "这块值得单独开一个话题")

    upgraded = client.post(
        f"/blocks/{block_id}/upgrade", json={"created_by": "cheese"}
    ).json()["data"]
    _wait_work_idle()  # kickoff runs in the background; don't race its writes

    assert _roster(client, upgraded["id"]).get("alice") == "owner"


def test_upgraded_block_without_a_creator_is_not_ownerless(client):
    """The web UI sends `created_by: ""` when its session token is missing, and
    this route resolves no actor of its own — it trusts the body verbatim."""
    p = _project(client, owner="alice")
    room = _create_topic(client, p["id"], headers=session_auth_headers("alice"))
    block_id = _insert_block(client, p["id"], room["id"], "这块值得单独开一个话题")

    upgraded = client.post(f"/blocks/{block_id}/upgrade", json={}).json()["data"]
    _wait_work_idle()

    assert _roster(client, upgraded["id"]).get("alice") == "owner"


def test_owner_can_manage_roster_of_an_agent_created_topic(client):
    """The functional consequence of the fix: with an owner seeded, a human can
    actually add members to a room 芝士 opened. Before, this was a 403 with no
    way out — no owner existed to grant anyone anything."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], json={"created_by": "cheese"})

    r = client.post(
        f"/topics/{topic['id']}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert _roster(client, topic["id"]).get("bob") == "member"


def test_project_lead_can_rescue_a_room_that_lost_its_owner(client):
    """The way out for the 96 rooms already stuck: a project lead may appoint an
    owner while the room has none. Before this, the only fix was a DB script."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=session_auth_headers("alice"))
    _orphan_the_roster(client, topic["id"])
    assert "owner" not in _roster(client, topic["id"]).values()

    _add_project_member(client, p["id"], "dana", "lead")
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

    _add_project_member(client, p["id"], "erin", "member")
    r = client.post(
        f"/topics/{topic['id']}/members",
        json={"handle": "erin", "role": "owner", "actor": "erin"},
        headers=session_auth_headers("erin"),
    )
    assert r.status_code == 403


def test_hatch_closes_once_the_room_has_an_owner_again(client):
    """A healthy room's owner is never overridden — the lead loses the power the
    moment the room can manage itself, so this isn't a blanket project-wide key."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=session_auth_headers("alice"))
    _add_project_member(client, p["id"], "dana", "lead")

    r = client.post(
        f"/topics/{topic['id']}/members",
        json={"handle": "mallory", "role": "member", "actor": "dana"},
        headers=session_auth_headers("dana"),
    )
    assert r.status_code == 403
