"""Integration tests for the Accept-card / Review domain (spec §4.4, §6.3).

Exercises the 验收 state machine through the FastAPI TestClient on an in-memory
SQLite DB: create card -> accept (archives topic), the AI-can't-accept-own
rule, double-accept, reject, and revoke (un-archives topic).
"""

import uuid


def _make_project(client) -> str:
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post(
        "/api/topics", json={"project_id": project_id, "title": "做一个东西"}
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card(client, topic_id: str, reviewer: str = "alice") -> str:
    r = client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": reviewer, "routing_reason": "最懂"},
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "pending"
    assert card["reviewer_handle"] == reviewer
    assert card["routing_reason"] == "最懂"
    return card["id"]


def test_create_card_404_for_missing_topic(client):
    r = client.post(
        f"/api/topics/{uuid.uuid4()}/accept-card",
        json={"reviewer_handle": "alice"},
    )
    assert r.status_code == 404


def test_list_cards_newest_first(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    first = _make_card(client, tid, "alice")
    # One pending card per topic (not a broadcast): reject the first before a
    # second can be filed.
    client.post(f"/api/accept-cards/{first}/reject", json={"decided_by": "alice"})
    second = _make_card(client, tid, "bob")

    r = client.get(f"/api/topics/{tid}/accept-card")
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["total"] == 2
    items = body["data"]["data"]
    assert len(items) == 2
    # Newest first.
    assert items[0]["id"] == second
    assert items[0]["reviewer_handle"] == "bob"


def test_accept_happy_path_archives_topic(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    r = client.post(f"/api/accept-cards/{cid}/accept", json={"decided_by": "alice"})
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "accepted"
    assert card["decided_by"] == "alice"
    assert card["decided_at"] is not None

    # 采纳即归档 (spec §6.3): topic now archived with accept markers.
    r = client.get(f"/api/topics/{tid}")
    topic = r.json()["data"]
    assert topic["status"] == "archived"

    cards = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "accepted"


def test_merge_exception_leaves_card_and_topic_retryable(client, monkeypatch):
    from app.domain.workspace import service as ws

    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    def fail_merge(*_args):
        raise RuntimeError("git object database unavailable")

    monkeypatch.setattr(ws, "merge_topic", fail_merge)

    r = client.post(f"/api/accept-cards/{cid}/accept", json={"decided_by": "alice"})

    assert r.status_code == 422
    assert "could not be merged" in r.json()["message"]
    cards = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"
    assert cards[0]["approvals"] == []
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"


def test_ai_cannot_accept_own_work_collaborative(client):
    # Default project ai_mode is collaborative.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    r = client.post(f"/api/accept-cards/{cid}/accept", json={"decided_by": "cheese"})
    assert r.status_code == 422
    assert "AI 不能验收自己做的东西" in r.json()["message"]

    # Card untouched, topic still active.
    cards = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"


def test_double_accept_422(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    assert (
        client.post(
            f"/api/accept-cards/{cid}/accept", json={"decided_by": "alice"}
        ).status_code
        == 200
    )
    r = client.post(f"/api/accept-cards/{cid}/accept", json={"decided_by": "alice"})
    assert r.status_code == 422


def test_accept_404_for_missing_card(client):
    r = client.post(
        f"/api/accept-cards/{uuid.uuid4()}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 404


def test_reject_keeps_topic_active(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    r = client.post(
        f"/api/accept-cards/{cid}/reject",
        json={"decided_by": "bob", "note": "数据不够"},
    )
    assert r.status_code == 200
    card = r.json()["data"]
    assert card["status"] == "rejected"
    assert card["decided_by"] == "bob"
    assert card["note"] == "数据不够"

    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"


def test_reject_then_accept_422(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    client.post(f"/api/accept-cards/{cid}/reject", json={"decided_by": "bob"})
    r = client.post(f"/api/accept-cards/{cid}/accept", json={"decided_by": "alice"})
    assert r.status_code == 422


def test_revoke_accepted_card_unarchives_topic(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    client.post(f"/api/accept-cards/{cid}/accept", json={"decided_by": "alice"})
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"

    r = client.post(f"/api/accept-cards/{cid}/revoke", json={"decided_by": "alice"})
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "revoked"

    # Topic back to active (spec §6.3: accept is revocable).
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"


def test_revoke_non_accepted_card_422(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)

    # Pending card cannot be revoked.
    r = client.post(f"/api/accept-cards/{cid}/revoke", json={"decided_by": "alice"})
    assert r.status_code == 422


def test_only_one_pending_card_per_topic(client):
    # 不是广播 (spec §4.4): a second pending card on the same topic is rejected.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _make_card(client, tid, "alice")
    r = client.post(
        f"/api/topics/{tid}/accept-card",
        json={"reviewer_handle": "bob", "routing_reason": "x"},
    )
    assert r.status_code == 422


def test_no_new_card_on_archived_topic(client):
    # 采纳一次性 (spec §6.3): after accept the topic is frozen — no new card.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    client.post(f"/api/accept-cards/{cid}/accept", json={"decided_by": "alice"})
    r = client.post(
        f"/api/topics/{tid}/accept-card",
        json={"reviewer_handle": "bob", "routing_reason": "x"},
    )
    assert r.status_code == 422


def test_revoke_requires_authority(client):
    # Only the accepter (or owner/lead) can revoke — not any handle.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid)
    client.post(f"/api/accept-cards/{cid}/accept", json={"decided_by": "alice"})

    r = client.post(f"/api/accept-cards/{cid}/revoke", json={"decided_by": "stranger"})
    assert r.status_code == 422
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"

    r = client.post(f"/api/accept-cards/{cid}/revoke", json={"decided_by": "alice"})
    assert r.status_code == 200
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"


def test_revoke_404_for_missing_card(client):
    r = client.post(
        f"/api/accept-cards/{uuid.uuid4()}/revoke", json={"decided_by": "alice"}
    )
    assert r.status_code == 404
