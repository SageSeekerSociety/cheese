"""Topic membership (话题成员名册) CRUD + permissions over HTTP."""

import uuid

from app.domain.identity.handles import topic_agent_handle

MISSING_TOPIC = "00000000-0000-0000-0000-000000000000"


def _agent(tid: str) -> str:
    """The handle THIS topic's 分身 sits in the roster under (分身独立身份)."""
    return topic_agent_handle(uuid.UUID(tid))


def _topic(client, created_by: str = "alice") -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": created_by},
    ).json()["data"]
    return t["id"]


def _roster(client, tid: str) -> list[dict]:
    return client.get(f"/topics/{tid}/members").json()["data"]["data"]


def test_seed_creator_owner_and_cheese_member(client):
    """A newborn topic seeds its roster: creator = owner, 芝士 = member.

    芝士 joins as THIS topic's own 分身 rather than the shared platform account,
    so the seat identifies one 分身 and can be taken from it alone."""
    tid = _topic(client, created_by="alice")
    members = {m["member_handle"]: m for m in _roster(client, tid)}
    assert members["alice"]["role"] == "owner"
    assert members[_agent(tid)]["role"] == "member"
    assert members[_agent(tid)]["agent"] is True
    assert members[_agent(tid)]["name"] == "芝士"
    assert members["alice"]["agent"] is False


def test_owner_can_add_member(client):
    tid = _topic(client, created_by="alice")
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["member_handle"] == "bob"
    handles = {m["member_handle"] for m in _roster(client, tid)}
    assert handles == {"alice", _agent(tid), "bob"}


def test_non_manager_cannot_add_member(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    # bob is a plain member → may not add anyone.
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "carol", "role": "member", "actor": "bob"},
    )
    assert r.status_code == 403


def test_admin_can_manage_but_stranger_cannot(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "admin", "actor": "alice"},
    )
    # admin bob adds carol
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "carol", "role": "member", "actor": "bob"},
    )
    assert r.status_code == 200
    # a non-member stranger cannot
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "dave", "role": "member", "actor": "stranger"},
    )
    assert r.status_code == 403


def test_duplicate_member_rejected(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
    )
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
    )
    assert r.status_code == 422


def test_update_role(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    r = client.put(
        f"/topics/{tid}/members/bob",
        json={"role": "admin", "actor": "alice"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["role"] == "admin"


def test_non_manager_cannot_update_role(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "member", "actor": "alice"},
    )
    r = client.put(
        f"/topics/{tid}/members/cheese",
        json={"role": "admin", "actor": "bob"},
    )
    assert r.status_code == 403


def test_cannot_remove_last_owner(client):
    tid = _topic(client, created_by="alice")
    r = client.delete(f"/topics/{tid}/members/alice?actor=alice")
    assert r.status_code == 422


def test_cannot_demote_last_owner(client):
    tid = _topic(client, created_by="alice")
    r = client.put(
        f"/topics/{tid}/members/alice",
        json={"role": "member", "actor": "alice"},
    )
    assert r.status_code == 422


def test_remove_member(client):
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "actor": "alice"},
    )
    r = client.delete(f"/topics/{tid}/members/bob?actor=alice")
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] is True
    handles = {m["member_handle"] for m in _roster(client, tid)}
    assert "bob" not in handles


def test_second_owner_lets_first_be_removed(client):
    """The last-owner guard is about the COUNT — promote a second owner and the
    first can leave."""
    tid = _topic(client, created_by="alice")
    client.post(
        f"/topics/{tid}/members",
        json={"handle": "bob", "role": "owner", "actor": "alice"},
    )
    r = client.delete(f"/topics/{tid}/members/alice?actor=bob")
    assert r.status_code == 200


def test_endpoints_require_existing_topic(client):
    r = client.get(f"/topics/{MISSING_TOPIC}/members")
    assert r.status_code == 404
    r = client.post(
        f"/topics/{MISSING_TOPIC}/members",
        json={"handle": "bob", "actor": "alice"},
    )
    assert r.status_code == 404
