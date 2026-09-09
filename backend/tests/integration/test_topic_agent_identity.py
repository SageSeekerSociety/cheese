"""每个话题的分身有自己的身份 (分身独立身份).

Before this, every 分身 on the platform acted as ONE ``cheese`` user and its
per-turn token said only *which topic* it was scoped to — never *who*. Two
consequences, both load-bearing:

* nothing a 分身 did was attributable to that 分身;
* de-authorizing one meant waiting out the token TTL, because there was no
  per-分身 thing to take away.

These tests pin the behaviour that fixes both: a topic's 分身 is its own
agent-user, its token names it, and its roster seat is the (single) place that
grants it access — which is what the follow-up authz work will enforce on.
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.identity.handles import CHEESE_HANDLE, topic_agent_handle
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import session_auth_headers


def _project_topic(client, owner: str = "alice") -> tuple[str, str]:
    p = client.post("/projects", json={"name": "P", "owner_handle": owner}).json()[
        "data"
    ]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": owner},
    ).json()["data"]
    return p["id"], t["id"]


def _members(client, topic_id: str) -> list[dict]:
    return client.get(f"/topics/{topic_id}/members").json()["data"]["data"]


def _agents(client, topic_id: str) -> list[dict]:
    return [m for m in _members(client, topic_id) if m["agent"]]


def _sandbox(project_id: str, topic_id: str) -> dict[str, str]:
    """The headers a 分身's sandbox sends: its per-turn scoped token."""
    return {
        "X-Cheese-Token": mint_scoped_token(project_id=project_id, topic_id=topic_id)
    }


# --- 身份 ---------------------------------------------------------------------


def test_a_new_topic_gets_its_own_agent_seat(client):
    """The room's agent is THIS topic's 分身, not the shared platform account."""
    _, tid = _project_topic(client)
    agents = _agents(client, tid)
    assert [m["member_handle"] for m in agents] == [topic_agent_handle(uuid.UUID(tid))]


def test_the_shared_platform_account_no_longer_sits_in_rooms(client):
    """Leaving it there would put two agents in one room and keep collapsing
    attribution onto the shared one."""
    _, tid = _project_topic(client)
    assert CHEESE_HANDLE not in {m["member_handle"] for m in _members(client, tid)}


def test_two_topics_get_two_different_agents(client):
    """The whole point: 分身 A and 分身 B are distinguishable."""
    pid, first = _project_topic(client)
    second = client.post(
        "/topics",
        json={"project_id": pid, "title": "T2", "created_by": "alice"},
    ).json()["data"]["id"]
    a = _agents(client, first)[0]["member_handle"]
    b = _agents(client, second)[0]["member_handle"]
    assert a != b


def test_identity_forks_but_the_displayed_name_does_not(client):
    """A reader still sees 芝士 — the raw ``cheese-<hex>`` handle is plumbing,
    and showing it would be a regression in the product, not a feature."""
    _, tid = _project_topic(client)
    agent = _agents(client, tid)[0]
    assert agent["name"] == "芝士"


# --- token 携带身份 -------------------------------------------------------------


def test_a_sandbox_token_acts_as_that_topics_agent(client):
    """A write made with the turn's scoped token is authored by the 分身 that
    holds it — previously every such write said plain ``cheese``."""
    pid, tid = _project_topic(client)
    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 分身写的", "expected_version": 0},
        headers=_sandbox(pid, tid),
    )
    assert r.status_code == 200
    assert r.json()["data"]["author"] == topic_agent_handle(uuid.UUID(tid))


def test_two_sandboxes_writing_are_told_apart(client):
    """Two 分身 acting in the same project leave two distinct authors behind —
    the property that makes an audit trail possible at all."""
    pid, first = _project_topic(client)
    second = client.post(
        "/topics",
        json={"project_id": pid, "title": "T2", "created_by": "alice"},
    ).json()["data"]["id"]

    authors = set()
    for tid in (first, second):
        r = client.put(
            f"/topics/{tid}/doc",
            json={"content": "# hi", "expected_version": 0},
            headers=_sandbox(pid, tid),
        )
        assert r.status_code == 200
        authors.add(r.json()["data"]["author"])
    assert len(authors) == 2


def test_a_forged_author_in_the_body_is_still_ignored(client):
    """The identity comes from the signed token, never the payload."""
    pid, tid = _project_topic(client)
    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# hi", "author": "alice", "expected_version": 0},
        headers=_sandbox(pid, tid),
    )
    assert r.json()["data"]["author"] == topic_agent_handle(uuid.UUID(tid))


def test_the_project_roster_marks_an_agent_without_matching_its_handle(client):
    """The DM list and the @ menu badge/skip 芝士 off this flag. They used to
    compare the handle to the literal ``cheese``; every 分身 now carries a
    per-topic handle, so a string match would offer you a 1:1 DM with 分身
    ``cheese-<hex>`` and drop its Agent badge."""
    pid, tid = _project_topic(client)
    handle = topic_agent_handle(uuid.UUID(tid))
    # The project-member write routes take the actor from the credential only —
    # a handle in the body is the forgery they exist to refuse — so act as the
    # project's owner rather than posting bare.
    for who in (handle, "alice"):
        assert (
            client.post(
                f"/projects/{pid}/members",
                json={"user_handle": who},
                headers=session_auth_headers("alice"),
            ).status_code
            == 200
        )

    rows = {
        m["user_handle"]: m
        for m in client.get(f"/projects/{pid}/members").json()["data"]["data"]
    }
    assert rows[handle]["agent"] is True
    assert rows["alice"]["agent"] is False


# --- 可撤权 --------------------------------------------------------------------


def test_one_agents_seat_can_be_dropped_without_touching_the_others(client):
    """撤权的抓手: 分身 identity is granted by a roster seat, so revoking one is a
    row delete scoped to one topic — instead of the old "wait out the 1h TTL,
    and it would have hit every 分身 anyway"."""
    pid, first = _project_topic(client)
    second = client.post(
        "/topics",
        json={"project_id": pid, "title": "T2", "created_by": "alice"},
    ).json()["data"]["id"]
    doomed = _agents(client, first)[0]["member_handle"]

    r = client.delete(
        f"/topics/{first}/members/{doomed}?actor=alice",
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200

    assert doomed not in {m["member_handle"] for m in _members(client, first)}
    assert len(_agents(client, second)) == 1


# --- house rules follow the identity, not the string ---------------------------


def test_a_topic_agent_cannot_accept_its_own_work(client):
    """协作模式下「AI 不能验收自己做的东西」matched the literal handle ``cheese``.
    Give each 分身 its own handle and that rule silently stops applying — which
    would have turned this change into a way around the red line."""
    pid, tid = _project_topic(client)
    handle = topic_agent_handle(uuid.UUID(tid))
    card = client.post(
        f"/topics/{tid}/tasks/{delivery_task_id(client, tid)}/accept-card",
        headers=delivery_headers(client, tid),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": handle,
            "routing_reason": "自己验",
        },
    ).json()["data"]

    r = client.post(
        f"/accept-cards/{card['id']}/accept",
        json={"decided_by": handle},
        headers=_sandbox(pid, tid),
    )
    assert r.status_code == 422
    assert "AI 不能验收自己做的东西" in r.json()["message"]
