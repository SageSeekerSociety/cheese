"""The machine quality gate is RETIRED (docs/accept-is-merge.md #296 → #718).

Filing an accept card used to run a project-configured command in the topic
workspace BEFORE the card reached a reviewer (born `pending_gate`, green →
`pending`, red → `gate_failed`). That whole mechanism — and, since #718, the
setting that configured it — is gone: a card is the platform's view of a PR,
and the real CI on that PR is what decides whether a change is good.

These tests pin the retirement as observable behaviour: a card is born
`pending` with no gate fields ever set, is immediately usable, and the
card-lifecycle rules (one live card per topic, terminal states stay terminal)
hold without any gate in between.
"""

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import session_auth_headers


def _authed(client):
    client.headers.update(session_auth_headers("alice"))


def _make_project(client) -> str:
    _authed(client)
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _file_card(client, topic_id: str, reviewer: str = "alice") -> dict:
    r = client.post(
        f"/topics/{topic_id}/tasks/{delivery_task_id(client, topic_id)}/accept-card",
        headers=delivery_headers(client, topic_id),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200
    return r.json()["data"]


def _latest_card(client, topic_id: str) -> dict:
    cards = client.get(f"/topics/{topic_id}/accept-card").json()["data"]["data"]
    assert cards
    return cards[0]


def test_card_born_pending_with_no_gate(client):
    """No platform check runs between filing and the reviewer: the card is born
    `pending` (never `pending_gate`) and its gate fields stay empty."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _file_card(client, tid)
    assert card["status"] == "pending"
    assert card["gate_passed_at"] is None
    assert card["gate_output"] == ""

    # Directly usable — no gate to wait on, no green to earn from the platform.
    assert _latest_card(client, tid)["status"] == "pending"


def test_a_pending_card_is_acceptable_immediately(client):
    """No gate stands between filing and accepting (local merge, since no
    GitHub App is configured in the test env)."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    card = _file_card(client, tid)
    assert card["status"] == "pending"

    r = client.post(f"/accept-cards/{card['id']}/accept", json={"decided_by": "alice"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"


def test_second_card_still_blocked_while_first_is_pending(client):
    """One live card per topic — unchanged. (Used to also cover pending_gate;
    that state is no longer reachable, so a plain pending card carries it.)"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    first = _file_card(client, tid)
    assert first["status"] == "pending"

    r = client.post(
        f"/topics/{tid}/tasks/{delivery_task_id(client, tid)}/accept-card",
        headers=delivery_headers(client, tid),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "bob",
            "routing_reason": "x",
        },
    )
    assert r.status_code == 422
    assert "改验收人" in r.json()["message"]


def test_an_accepted_card_still_cannot_be_rejected(client):
    # Widening reject must not turn it into a way around the terminal states.
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    card = _file_card(client, tid)
    assert card["status"] == "pending"

    r = client.post(f"/accept-cards/{card['id']}/accept", json={"decided_by": "alice"})
    assert r.status_code == 200, r.text

    r = client.post(
        f"/accept-cards/{card['id']}/reject",
        json={"decided_by": "alice", "note": "x"},
    )
    assert r.status_code == 422
    assert "已处理" in r.text
