"""每个 AI 队友有自己的身份 (分身独立身份).

Before this, every 分身 on the platform acted as ONE ``cheese`` user and its
per-turn token said only *which topic* it was scoped to — never *who*. Two
consequences, both load-bearing:

* nothing a 分身 did was attributable to that 分身;
* de-authorizing one meant waiting out the token TTL, because there was no
  per-分身 thing to take away.

These tests pin the behaviour that fixes both: a teammate is its own
agent-user, the same one in every room it sits in; a token names who is
acting; and a roster seat is the (single) place that grants it access to a
room — a room does not have an agent of its own.
"""

from app.core.sandbox_auth import mint_scoped_token
from app.domain.identity.handles import CHEESE_HANDLE
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


def _seat(client, topic_id: str) -> str:
    """The seat of the one agent in this room."""
    seats = [m["member_handle"] for m in _agents(client, topic_id)]
    assert len(seats) == 1, seats
    return seats[0]


def _sandbox(project_id: str, topic_id: str, seat: str) -> dict[str, str]:
    """The headers an agent's sandbox sends: its per-turn scoped token, naming
    the agent it acts as."""
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project_id, topic_id=topic_id, agent_handle=seat
        )
    }


# --- 身份 ---------------------------------------------------------------------


def test_a_new_room_seats_one_agent_named_for_the_projects_default(client):
    """A new room has exactly one agent row, and it is the project's default
    teammate by name — not the shared platform account."""
    pid, tid = _project_topic(client)
    default = next(
        a
        for a in client.get(f"/projects/{pid}/agents").json()["data"]["data"]
        if a["is_default"]
    )
    rows = _agents(client, tid)
    assert [m["name"] for m in rows] == [default["display_name"]]
    assert rows[0]["member_handle"] != CHEESE_HANDLE


def test_the_shared_platform_account_no_longer_sits_in_rooms(client):
    """Leaving it there would put two agents in one room and keep collapsing
    attribution onto the shared one."""
    _, tid = _project_topic(client)
    assert CHEESE_HANDLE not in {m["member_handle"] for m in _members(client, tid)}


def test_identity_forks_but_the_displayed_name_does_not(client):
    """A reader still sees 芝士 — the raw ``cheese-<hex>`` handle is plumbing,
    and showing it would be a regression in the product, not a feature."""
    _, tid = _project_topic(client)
    agent = _agents(client, tid)[0]
    assert agent["name"] == "芝士"


# --- token 携带身份 -------------------------------------------------------------


def test_a_sandbox_token_acts_as_the_agent_it_names(client):
    """A write made with the turn's scoped token is authored by the agent that
    holds it — previously every such write said plain ``cheese``."""
    pid, tid = _project_topic(client)
    seat = _seat(client, tid)
    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 分身写的", "expected_version": 0},
        headers=_sandbox(pid, tid, seat),
    )
    assert r.status_code == 200
    assert r.json()["data"]["author"] == seat


def test_two_agents_writing_in_one_room_are_told_apart(client):
    """Two teammates acting in the same room leave two distinct authors behind —
    the property that makes an audit trail possible at all."""
    pid, tid = _project_topic(client)
    ops = client.post(f"/projects/{pid}/agents", json={"handle": "ops"}).json()["data"]
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": ops["seat_handle"], "role": "member", "actor": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    default_seat = next(
        m["member_handle"]
        for m in _agents(client, tid)
        if m["member_handle"] != ops["seat_handle"]
    )

    authors = []
    for version, seat in enumerate((default_seat, ops["seat_handle"])):
        r = client.put(
            f"/topics/{tid}/doc",
            json={"content": f"# {seat}", "expected_version": version},
            headers=_sandbox(pid, tid, seat),
        )
        assert r.status_code == 200, r.text
        authors.append(r.json()["data"]["author"])
    assert authors == [default_seat, ops["seat_handle"]]


def test_a_forged_author_in_the_body_is_still_ignored(client):
    """The identity comes from the signed token, never the payload."""
    pid, tid = _project_topic(client)
    seat = _seat(client, tid)
    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# hi", "author": "alice", "expected_version": 0},
        headers=_sandbox(pid, tid, seat),
    )
    assert r.json()["data"]["author"] == seat


def test_the_project_roster_marks_an_agent_without_matching_its_handle(client):
    """The DM list and the @ menu badge/skip 芝士 off this flag. They used to
    compare the handle to the literal ``cheese``; every 分身 now carries a
    per-topic handle, so a string match would offer you a 1:1 DM with 分身
    ``cheese-<hex>`` and drop its Agent badge."""
    pid, tid = _project_topic(client)
    handle = _seat(client, tid)
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
    handle = _seat(client, tid)
    card = client.post(
        f"/topics/{tid}/tasks/{delivery_task_id(client, tid)}/accept-card",
        headers=delivery_headers(client, tid),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": handle,
            "routing_reason": "自己验",
        },
    )
    assert card.status_code == 200, card.text
    card = card.json()["data"]

    r = client.post(
        f"/accept-cards/{card['id']}/accept",
        json={"decided_by": handle},
        headers=_sandbox(pid, tid, handle),
    )
    assert r.status_code == 422
    assert "AI 不能验收自己做的东西" in r.json()["message"]
