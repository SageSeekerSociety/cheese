"""改验收人 — reassign a pending accept card (spec §4.4)."""

from tests.integration.conftest import session_auth_headers


def _topic_and_card(client) -> str:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post("/api/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    card = client.post(
        f"/api/topics/{t['id']}/accept-card",
        json={"reviewer_handle": "user-1"},
    ).json()["data"]
    return card["id"]


def test_reassign_changes_reviewer(client):
    card_id = _topic_and_card(client)
    r = client.post(
        f"/api/accept-cards/{card_id}/reassign",
        json={"reviewer_handle": "mentor-1", "routing_reason": "导师更合适"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["reviewer_handle"] == "mentor-1"
    assert r.json()["data"]["routing_reason"] == "导师更合适"


def test_cannot_reassign_decided_card(client):
    card_id = _topic_and_card(client)
    client.post(
        f"/api/accept-cards/{card_id}/accept",
        json={"decided_by": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    r = client.post(
        f"/api/accept-cards/{card_id}/reassign",
        json={"reviewer_handle": "user-2"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
