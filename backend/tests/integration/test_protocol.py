"""Task Template protocol enforcement on accept (spec §4.2/§4.4)."""

from tests.conftest import seed_space
from tests.integration.conftest import session_auth_headers

OWNER = "owner-1"


def _setup_with_mentor_condition(client) -> tuple[str, str]:
    """Project linked to a Task whose template requires mentor acceptance for
    a 结题 topic. Returns (project_id, topic_id of a 结题答辩 topic)."""
    space_id = seed_space(client, "信院")
    tmpl = client.post(
        f"/api/spaces/{space_id}/templates",
        json={
            "name": "创研课",
            "conditions": [{"required_topic": "结题", "reviewer_role": "mentor"}],
        },
    ).json()["data"]
    task = client.post(
        f"/api/templates/{tmpl['id']}/tasks", json={"title": "题目"}
    ).json()["data"]
    p = client.post(
        "/api/projects", json={"name": "团队", "owner_handle": OWNER}
    ).json()["data"]
    pid = p["id"]
    client.post(f"/api/projects/{pid}/tasks", json={"task_id": task["id"]})
    # Roster writes are authorized against a token — go out as the project owner.
    client.post(
        f"/api/projects/{pid}/members",
        json={"user_handle": "mentor-1", "role": "mentor"},
        headers=session_auth_headers(OWNER),
    )
    client.post(
        f"/api/projects/{pid}/members",
        json={"user_handle": "user-1"},
        headers=session_auth_headers(OWNER),
    )
    topic = client.post(
        "/api/topics", json={"project_id": pid, "title": "结题答辩"}
    ).json()["data"]
    return pid, topic["id"]


def _card(client, topic_id: str, reviewer: str) -> str:
    return client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={"reviewer_handle": reviewer},
    ).json()["data"]["id"]


def test_non_mentor_cannot_accept_protocol_topic(client):
    # Card routed to the non-mentor themself: still blocked by the mentor
    # protocol, distinct from (and on top of) the "must be the routed
    # reviewer" identity check.
    _, tid = _setup_with_mentor_condition(client)
    card = _card(client, tid, "user-1")
    r = client.post(
        f"/api/accept-cards/{card}/accept",
        json={"decided_by": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 422  # 须导师验收


def test_mentor_can_accept(client):
    _, tid = _setup_with_mentor_condition(client)
    card = _card(client, tid, "mentor-1")
    r = client.post(
        f"/api/accept-cards/{card}/accept",
        json={"decided_by": "mentor-1"},
        headers=session_auth_headers("mentor-1"),
    )
    assert r.status_code == 200
    topic = client.get(f"/api/topics/{tid}").json()["data"]
    assert topic["status"] == "archived"
