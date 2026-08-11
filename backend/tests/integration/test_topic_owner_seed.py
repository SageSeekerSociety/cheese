"""POST /topics — a newborn room must never be ownerless (话题拥有者可能为空).

`seed()` deliberately refuses to make 芝士 the owner, and `create()` used to
pass `created_by` straight through: a topic created BY the agent, or by a caller
whose token didn't resolve, was born with 芝士 as its only member and NO owner —
so nobody could manage its roster (`can_manage_roster` needs owner/admin). Worse,
`split_to_subtopic` defaults a child's owner to the PARENT's owner, so one
ownerless room made every sub-topic under it ownerless too. On the dogfood
project this had reached 96 of 149 topics.

These tests pin the fallback ladder — real creator → parent room's owner →
project owner — and the escape hatch that gets the ALREADY-broken rooms out:
while a room has no manager at all, the project's owner/lead may appoint one.
Without it those rooms are a dead end with no route out of the product (only an
owner may appoint an owner, and there is none), repairable only by hand-editing
the database.
"""

import asyncio
import uuid

from sqlalchemy import delete

from app.core.tokens import mint_session_token
from app.domain.topic.models import TopicMembership, TopicRole


def _bearer(handle: str) -> dict:
    token = mint_session_token(handle=handle, user_id=None)
    return {"Authorization": f"Bearer {token}"}


def _project(client, owner: str | None) -> dict:
    body: dict = {"name": "P"}
    if owner is not None:
        body["owner_handle"] = owner
    return client.post("/api/projects", json=body).json()["data"]


def _roster(client, topic_id: str) -> dict[str, str]:
    rows = client.get(f"/api/topics/{topic_id}/members").json()["data"]["data"]
    return {m["member_handle"]: m["role"] for m in rows}


def _create_topic(client, project_id: str, **kw) -> dict:
    body = {"project_id": project_id, "title": "T", **kw.pop("json", {})}
    return client.post("/api/topics", json=body, **kw).json()["data"]


def _add_project_member(
    client, project_id: str, handle: str, role: str, *, actor: str = "alice"
) -> None:
    """Seed the project roster as its owner — writing it needs an owner/lead token
    now, so an anonymous POST here would silently 403 and leave the roster empty."""
    r = client.post(
        f"/api/projects/{project_id}/members",
        json={"user_handle": handle, "role": role},
        headers=_bearer(actor),
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


def test_topic_created_by_human_is_owned_by_that_human(client):
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=_bearer("alice"))

    assert _roster(client, topic["id"]).get("alice") == "owner"


def test_topic_created_by_cheese_falls_back_to_project_owner(client):
    """The bug users hit: 芝士 creating a room left it with no owner at all."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], json={"created_by": "cheese"})

    roster = _roster(client, topic["id"])
    assert roster.get("alice") == "owner"
    assert roster.get("cheese") == "member"


def test_topic_created_anonymously_still_gets_an_owner(client):
    """A token-less Phase-0 call (no `created_by` at all) — the shape the web UI
    degrades to when its session token is missing or expired."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"])

    assert _roster(client, topic["id"]).get("alice") == "owner"


def test_subtopic_under_agent_created_room_is_not_ownerless(client):
    """The cascade: an ownerless room used to make every sub-topic under it
    ownerless too, because the child's owner defaults to the parent's."""
    p = _project(client, owner="alice")
    room = _create_topic(client, p["id"], json={"created_by": "cheese"})

    child = client.post(
        f"/api/topics/{room['id']}/split",
        json={"title": "分身拆出的子任务", "created_by": "cheese"},
    ).json()["data"]

    assert _roster(client, child["id"]).get("alice") == "owner"


def test_owner_can_manage_roster_of_an_agent_created_topic(client):
    """The functional consequence of the fix: with an owner seeded, a human can
    actually add members to a room 芝士 opened. Before, this was a 403 with no
    way out — no owner existed to grant anyone anything."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], json={"created_by": "cheese"})

    r = client.post(
        f"/api/topics/{topic['id']}/members",
        json={"handle": "bob", "actor": "alice"},
        headers=_bearer("alice"),
    )
    assert r.status_code == 200
    assert _roster(client, topic["id"]).get("bob") == "member"


def test_project_lead_can_rescue_a_room_that_lost_its_owner(client):
    """The way out for the 96 rooms already stuck: a project lead may appoint an
    owner while the room has none. Before this, the only fix was a DB script."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=_bearer("alice"))
    _orphan_the_roster(client, topic["id"])
    assert "owner" not in _roster(client, topic["id"]).values()

    _add_project_member(client, p["id"], "dana", "lead")
    r = client.post(
        f"/api/topics/{topic['id']}/members",
        json={"handle": "dana", "role": "owner", "actor": "dana"},
        headers=_bearer("dana"),
    )
    assert r.status_code == 200
    assert _roster(client, topic["id"]).get("dana") == "owner"


def test_plain_project_member_cannot_rescue_a_room(client):
    """The hatch is for whoever answers for the project, not for everyone in it."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=_bearer("alice"))
    _orphan_the_roster(client, topic["id"])

    _add_project_member(client, p["id"], "erin", "member")
    r = client.post(
        f"/api/topics/{topic['id']}/members",
        json={"handle": "erin", "role": "owner", "actor": "erin"},
        headers=_bearer("erin"),
    )
    assert r.status_code == 403


def test_hatch_closes_once_the_room_has_an_owner_again(client):
    """A healthy room's owner is never overridden — the lead loses the power the
    moment the room can manage itself, so this isn't a blanket project-wide key."""
    p = _project(client, owner="alice")
    topic = _create_topic(client, p["id"], headers=_bearer("alice"))
    _add_project_member(client, p["id"], "dana", "lead")

    r = client.post(
        f"/api/topics/{topic['id']}/members",
        json={"handle": "mallory", "role": "member", "actor": "dana"},
        headers=_bearer("dana"),
    )
    assert r.status_code == 403
