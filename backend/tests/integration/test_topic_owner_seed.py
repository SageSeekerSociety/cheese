"""POST /topics — a newborn room must never be ownerless (话题拥有者可能为空).

`seed()` deliberately refuses to make 芝士 the owner, and `create()` used to
pass `created_by` straight through: a topic created BY the agent, or by a caller
whose token didn't resolve, was born with 芝士 as its only member and NO owner —
so nobody could manage its roster (`can_manage_roster` needs owner/admin). Worse,
`split_to_subtopic` defaults a child's owner to the PARENT's owner, so one
ownerless room made every sub-topic under it ownerless too. On the dogfood
project this had reached 89 of 123 topics.

These tests pin the fallback ladder: real creator → parent room's owner →
project owner.
"""

from app.core.tokens import mint_session_token


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
