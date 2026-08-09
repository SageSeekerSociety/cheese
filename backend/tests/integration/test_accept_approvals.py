"""Integration tests for 主分支保护 / N-人批准 (spec §4.4, eval C2).

A project may require `approvals_required` distinct human approvals before an
accept actually merges. Default is 1 and the accepter's own accept counts as
their approval — so unconfigured projects keep the old behavior exactly.
AI cannot vote (collaborative mode, same rule as "AI 不能验收自己").
"""

import pytest

from app.core.tokens import mint_session_token


@pytest.fixture(autouse=True)
def _authenticated_project_owner(client):
    client.headers["Authorization"] = (
        f"Bearer {mint_session_token(handle='alice', user_id=None)}"
    )
    yield
    client.headers.pop("Authorization", None)


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


def _make_card(client, topic_id: str, reviewer: str = "alice") -> dict:
    r = client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": reviewer, "routing_reason": "最懂"},
    )
    assert r.status_code == 200
    return r.json()["data"]


def _require(client, project_id: str, n: int) -> None:
    r = client.put(
        f"/api/projects/{project_id}/quality-gate", json={"approvals_required": n}
    )
    assert r.status_code == 200


def _approve(client, card_id: str, handle: str):
    return client.post(
        f"/api/accept-cards/{card_id}/approve",
        json={"approver_handle": handle},
        headers={
            "Authorization": f"Bearer {mint_session_token(handle=handle, user_id=None)}"
        },
    )


def test_default_single_approval_backward_compat(client):
    # No settings → accept goes through directly, as before.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _make_card(client, tid)
    assert card["approvals_required"] == 1
    assert card["approvals"] == []

    r = client.post(
        f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 200
    out = r.json()["data"]
    assert out["status"] == "accepted"
    # The accept itself counted as alice's approval.
    assert out["approvals"] == ["alice"]


def test_accept_short_of_votes_structured_error(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _require(client, pid, 3)
    card = _make_card(client, tid)
    assert card["approvals_required"] == 3

    r = client.post(
        f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 422
    # Structured shortfall the frontend can display: 还差 N 票 (alice's own
    # accept would count as 1 of 3).
    assert "还差 2 票" in r.json()["message"]

    # Card untouched, topic still active.
    cards = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "active"


def test_approvals_then_accept_merges(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _require(client, pid, 2)
    card = _make_card(client, tid)

    r = _approve(client, card["id"], "bob")
    assert r.status_code == 200
    assert r.json()["data"]["approvals"] == ["bob"]

    # bob's vote + alice's accept = 2/2 → the accept executes.
    r = client.post(
        f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 200
    out = r.json()["data"]
    assert out["status"] == "accepted"
    assert sorted(out["approvals"]) == ["alice", "bob"]
    assert client.get(f"/api/topics/{tid}").json()["data"]["status"] == "archived"


def test_ai_cannot_approve_collaborative(client):
    # Default project ai_mode is collaborative — same red line as accepting.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _make_card(client, tid)

    r = _approve(client, card["id"], "cheese")
    assert r.status_code == 422
    assert "AI 不能" in r.json()["message"]

    cards = client.get(f"/api/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["approvals"] == []


def test_duplicate_approve_idempotent(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _make_card(client, tid)

    assert _approve(client, card["id"], "bob").status_code == 200
    r = _approve(client, card["id"], "bob")
    assert r.status_code == 200
    assert r.json()["data"]["approvals"] == ["bob"]


def test_approve_decided_card_422(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _make_card(client, tid)
    client.post(f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"})

    r = _approve(client, card["id"], "bob")
    assert r.status_code == 422


def test_revoke_keeps_approvals_history(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _require(client, pid, 2)
    card = _make_card(client, tid)
    _approve(client, card["id"], "bob")
    client.post(f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"})

    r = client.post(
        f"/api/accept-cards/{card['id']}/revoke", json={"decided_by": "alice"}
    )
    assert r.status_code == 200
    # 撤销不清票 — the vote trail is history.
    assert sorted(r.json()["data"]["approvals"]) == ["alice", "bob"]
