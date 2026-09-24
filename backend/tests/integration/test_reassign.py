"""改验收人 — reassign a pending accept card (spec §4.4)."""

from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_accept import _make_card
from tests.integration.test_accept import remote_delivery as remote_delivery
from tests.integration.test_accept_pr import _rendered_head
from tests.integration.test_accept_pr import app_world as app_world


def _topic_and_card(client) -> str:
    p = post_project(client, json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return _make_card(client, t["id"], "user-1")


def test_reassign_changes_reviewer(client):
    card_id = _topic_and_card(client)
    r = client.post(
        f"/accept-cards/{card_id}/reassign",
        json={"reviewer_handle": "mentor-1", "routing_reason": "导师更合适"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["reviewer_handle"] == "mentor-1"
    assert r.json()["data"]["routing_reason"] == "导师更合适"


def test_cannot_reassign_decided_card(client):
    card_id = _topic_and_card(client)
    accepted = client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": "user-1", "head_sha": _rendered_head(client, card_id)},
        headers=session_auth_headers("user-1"),
    )
    assert accepted.status_code == 200, accepted.text
    r = client.post(
        f"/accept-cards/{card_id}/reassign",
        json={"reviewer_handle": "user-2"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 422
