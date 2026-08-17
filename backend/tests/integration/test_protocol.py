"""机构协议 enforcement on accept (spec §4.2/§4.4, #370).

The terms now live on the 项目集 (`space_categories`) with a per-赛题 override,
and a project accepts them by being created FROM the 赛题 — the link the 赛题
page's button writes. This used to go through cheesex `task_templates` and a
`project_task_links` row, a parallel 题目 hierarchy with no way to create it
from the UI.
"""

from tests.conftest import seed_task_with_protocol
from tests.integration.conftest import session_auth_headers

OWNER = "owner-1"


def _setup_with_mentor_condition(client) -> tuple[str, str]:
    """A project created from a 赛题 whose 项目集 requires a mentor to accept a
    结题 topic. Returns (project_id, topic_id of a 结题答辩 topic)."""
    task_id = seed_task_with_protocol(
        client, conditions=[{"required_topic": "结题", "reviewer_role": "mentor"}]
    )
    p = client.post(
        "/projects",
        json={"name": "团队", "owner_handle": OWNER, "external_task_id": task_id},
    ).json()["data"]
    pid = p["id"]
    # Roster writes are authorized against a token — go out as the project owner.
    client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "mentor-1", "role": "mentor"},
        headers=session_auth_headers(OWNER),
    )
    client.post(
        f"/projects/{pid}/members",
        json={"user_handle": "user-1"},
        headers=session_auth_headers(OWNER),
    )
    topic = client.post(
        "/topics", json={"project_id": pid, "title": "结题答辩"}
    ).json()["data"]
    return pid, topic["id"]


def _card(client, topic_id: str, reviewer: str) -> str:
    return client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
        },
    ).json()["data"]["id"]


def test_non_mentor_cannot_accept_protocol_topic(client):
    # Card routed to the non-mentor themself: still blocked by the mentor
    # protocol, distinct from (and on top of) the "must be the routed
    # reviewer" identity check.
    _, tid = _setup_with_mentor_condition(client)
    card = _card(client, tid, "user-1")
    r = client.post(
        f"/accept-cards/{card}/accept",
        json={"decided_by": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert r.status_code == 422  # 须导师验收


def test_mentor_can_accept(client):
    _, tid = _setup_with_mentor_condition(client)
    card = _card(client, tid, "mentor-1")
    r = client.post(
        f"/accept-cards/{card}/accept",
        json={"decided_by": "mentor-1"},
        headers=session_auth_headers("mentor-1"),
    )
    assert r.status_code == 200
    topic = client.get(f"/topics/{tid}").json()["data"]
    assert topic["status"] == "active"  # 交付完成不归档 (#442 decision 1)
    assert topic["accepted_by"] == "mentor-1"
