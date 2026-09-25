"""Integration tests for 主分支保护 / N-人批准 (spec §4.4, eval C2).

A project may require `approvals_required` distinct human approvals before an
accept actually merges. Default is 1 and the accepter's own accept counts as
their approval — so unconfigured projects keep the old behavior exactly.
AI cannot vote (collaborative mode, same rule as "AI 不能验收自己").
"""

import pytest

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_accept import _make_card as _remote_card
from tests.integration.test_accept import remote_delivery as remote_delivery
from tests.integration.test_accept_pr import _rendered_head
from tests.integration.test_accept_pr import app_world as app_world


@pytest.fixture(autouse=True)
def _authenticated_project_owner(client):
    client.headers.update(session_auth_headers("alice"))
    yield
    client.headers.pop("Authorization", None)


def _make_project(client) -> str:
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card(client, topic_id: str, reviewer: str = "alice") -> dict:
    card_id = _remote_card(client, topic_id, reviewer)
    return next(
        card
        for card in client.get(f"/topics/{topic_id}/accept-card").json()["data"]["data"]
        if card["id"] == card_id
    )


def _require(client, project_id: str, n: int) -> None:
    r = client.put(
        f"/projects/{project_id}/branch-protection", json={"approvals_required": n}
    )
    assert r.status_code == 200


def _approve(client, card_id: str, handle: str):
    return client.post(
        f"/accept-cards/{card_id}/approve",
        json={"approver_handle": handle, "head_sha": _rendered_head(client, card_id)},
        headers=session_auth_headers(handle),
    )


def test_default_single_approval_backward_compat(client):
    # No settings → accept goes through directly, as before.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _make_card(client, tid)
    assert card["approvals_required"] == 1
    assert card["approvals"] == []

    r = client.post(
        f"/accept-cards/{card['id']}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card["id"])},
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
        f"/accept-cards/{card['id']}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card["id"])},
    )
    assert r.status_code == 422
    # The refusal carries the tally: alice's own accept counts as 1 of 3.
    assert "1/3" in r.json()["message"]

    # Card untouched, topic still active.
    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


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
        f"/accept-cards/{card['id']}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card["id"])},
    )
    assert r.status_code == 200
    out = r.json()["data"]
    assert out["status"] == "accepted"
    assert sorted(out["approvals"]) == ["alice", "bob"]
    assert (
        client.get(
            f"/topics/{tid}/tasks/{delivery_task_id(client, tid)}",
            headers=delivery_headers(client, tid),
        ).json()["data"]["accepted_by"]
        == "alice"
    )


def test_ai_cannot_approve_collaborative(client):
    # Default project ai_mode is collaborative — same red line as accepting.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _make_card(client, tid)

    r = _approve(client, card["id"], "cheese")
    assert r.status_code == 422
    assert "AI 不能" in r.json()["message"]

    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
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
    client.post(
        f"/accept-cards/{card['id']}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card["id"])},
    )

    r = _approve(client, card["id"], "bob")
    assert r.status_code == 422


def test_revoke_keeps_approvals_history(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _require(client, pid, 2)
    card = _make_card(client, tid)
    _approve(client, card["id"], "bob")
    client.post(
        f"/accept-cards/{card['id']}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, card["id"])},
    )

    r = client.post(f"/accept-cards/{card['id']}/revoke", json={"decided_by": "alice"})
    assert r.status_code == 200
    # 撤销不清票 — the vote trail is history.
    assert sorted(r.json()["data"]["approvals"]) == ["alice", "bob"]
