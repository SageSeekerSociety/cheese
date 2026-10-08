"""Security regression tests: accept-card decision endpoints must not let an
unauthenticated caller, an authenticated caller with no standing in the card's
room, or a member who isn't the routed reviewer decide someone else's card
(accept/reject/revoke/reassign/approve/void/merge-anyway/auto-merge).

Before the first fix, `decided_by`/`approver_handle` were trusted straight from
the request body with no authentication at all — anyone who could reach the API
could accept/reject/revoke any card by simply naming the right handle.

The second fix (2026-09-26) closed the room-membership hole left behind by the
first one: the decision routes resolved an actor but never asked whether that
actor belonged to the card's room. Since `/reassign` takes the new reviewer
straight from the request body and the accept gate is
`decided_by == card.reviewer_handle`, any logged-in stranger could re-route
someone else's card to themselves and then accept it — handing a stranger the
room's merge, signed with the stranger's own name. `_card_actor` now draws the
same line `_task_actor` always drew (`authorize_topic`).
"""

import pytest

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)
from tests.integration.test_accept import _make_card as _make_card
from tests.integration.test_accept import remote_delivery as remote_delivery
from tests.integration.test_accept_pr import _rendered_head
from tests.integration.test_accept_pr import app_world as app_world


def _make_project(client) -> str:
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    pid = r.json()["data"]["id"]
    # alice is this file's legitimate actor: the routed reviewer of every card
    # here, and the accepter whose accept a revoke must be checked against. The
    # decision routes require room membership, so she is put on the project the
    # way a real participant is; mallory and bob stay outsiders by default, and
    # the cases that need them *inside* (to reach a rule that lives behind the
    # membership gate) say so themselves.
    join_project_team(client, pid, "alice")
    return pid


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def test_accept_without_auth_401(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    # No Authorization header at all — a fully anonymous caller.
    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, cid)},
    )
    assert r.status_code == 401

    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"


def test_accept_by_non_reviewer_403_even_with_matching_body_field(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")
    # mallory is a genuine member here so that this case still tests what it
    # says — the reviewer rule behind the membership gate. An outsider's 403 is
    # a different assertion, and has its own tests (below).
    join_project_team(client, pid, "mallory")

    # An authenticated attacker (mallory) tries to forge the body's decided_by
    # as "alice" — the resolved actor identity (mallory, from the verified
    # token) must win over the self-reported body field.
    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, cid)},
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 403
    assert "只有被指定审阅的人能采纳" in r.json()["message"]

    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


def test_accept_by_routed_reviewer_succeeds(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, cid)},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert r.json()["data"]["decided_by"] == "alice"


def test_reject_without_auth_401(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    r = client.post(f"/accept-cards/{cid}/reject", json={"decided_by": "alice"})
    assert r.status_code == 401


def test_reject_by_non_reviewer_403(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")
    # Same split as the accept case above: mallory is in the room, so what
    # refuses her is the reviewer rule ("not the routed reviewer"), not the
    # membership gate.
    join_project_team(client, pid, "mallory")

    r = client.post(
        f"/accept-cards/{cid}/reject",
        json={"decided_by": "mallory"},
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 403
    assert "只有被指定审阅的人能退回" in r.json()["message"]
    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"


def test_revoke_without_auth_401(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")
    client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, cid)},
        headers=session_auth_headers("alice"),
    )

    r = client.post(f"/accept-cards/{cid}/revoke", json={"decided_by": "alice"})
    assert r.status_code == 401
    assert (
        client.get(
            f"/topics/{delivery_task_id(client, tid)}/task",
            headers=delivery_headers(client, tid),
        ).json()["data"]["accepted_by"]
        == "alice"
    )


def test_revoke_ignores_spoofed_body_identity(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")
    client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice", "head_sha": _rendered_head(client, cid)},
        headers=session_auth_headers("alice"),
    )

    # mallory authenticates as herself but claims to be "alice" in the body —
    # the allow-list check must use the verified handle, not the body. She is a
    # project member so the request reaches that allow-list: for an outsider the
    # answer is 403 from the membership gate, which is a different assertion.
    join_project_team(client, pid, "mallory")
    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 422
    assert (
        client.get(
            f"/topics/{delivery_task_id(client, tid)}/task",
            headers=delivery_headers(client, tid),
        ).json()["data"]["accepted_by"]
        == "alice"
    )


def test_reassign_without_auth_401(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    r = client.post(
        f"/accept-cards/{cid}/reassign",
        json={"reviewer_handle": "mallory", "focus": "x"},
    )
    assert r.status_code == 401
    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["reviewer_handle"] == "alice"


def test_approve_without_auth_401(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    r = client.post(f"/accept-cards/{cid}/approve", json={"approver_handle": "alice"})
    assert r.status_code == 401


def test_approve_uses_verified_identity_not_body(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    # bob authenticates as himself but claims to be "alice" in the body.
    #
    # He is NOT a member of this room, and that is the answer: 403. Until
    # 2026-09-26 this asserted 200 with `approvals == ["bob"]`, i.e. the file
    # recorded "a logged-in stranger's vote counts" as the expected behaviour.
    # That was a defect, not a contract: an approval is one of the votes that
    # unlock 主分支保护, so a stranger's vote is a stranger deciding how many
    # people must look at the room's change before it merges. The identity
    # half of the case (the body's handle never wins) is still asserted — with
    # bob actually in the project, just below, where the vote is allowed.
    r = client.post(
        f"/accept-cards/{cid}/approve",
        json={"approver_handle": "alice"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 403
    assert (
        client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]["approvals"]
        == []
    )

    # Now let bob in: the forged body field must still lose to his real handle.
    join_project_team(client, pid, "bob")
    r = client.post(
        f"/accept-cards/{cid}/approve",
        json={"approver_handle": "alice"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["approvals"] == ["bob"]


def test_reassign_to_self_then_accept_is_refused_for_a_non_member(client):
    """The kill chain, end to end: re-route the card to yourself, then accept
    it as the reviewer you just appointed.

    Both halves are the same missing question — "are you in this room?" — and
    either one alone is enough to hand a stranger the merge: `/reassign` takes
    the reviewer from the request body, and `AcceptService.accept` only asks
    `decided_by == card.reviewer_handle`. Fixing reassign but not accept (or
    the reverse) would leave the chain walkable through the other pivot, which
    is why `_card_actor` is the one place it is answered.
    """
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    # mallory has no standing here at all — she cannot even read the project.
    assert (
        client.get(
            f"/projects/{pid}", headers=session_auth_headers("mallory")
        ).status_code
        == 403
    )

    r = client.post(
        f"/accept-cards/{cid}/reassign",
        json={"reviewer_handle": "mallory", "focus": "我最懂"},
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 403

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "mallory", "head_sha": _rendered_head(client, cid)},
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 403

    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"
    assert cards[0]["reviewer_handle"] == "alice"
    assert cards[0]["decided_by"] is None
    task = client.get(
        f"/topics/{delivery_task_id(client, tid)}/task",
        headers=delivery_headers(client, tid),
    ).json()["data"]
    assert task["accepted_by"] is None


# Every route that decides (or undecides) a card, with a body that would be
# accepted from the right person. mallory is in none of these rooms.
_CARD_DECISION_ROUTES = [
    ("accept", {"decided_by": "mallory"}),
    ("reject", {"decided_by": "mallory", "note": "不行"}),
    ("reassign", {"reviewer_handle": "mallory", "focus": "我最懂"}),
    ("void", {"decided_by": "mallory", "note": "作废"}),
    ("approve", {"approver_handle": "mallory"}),
    ("revoke", {"decided_by": "mallory"}),
    ("merge-anyway", {"decided_by": "mallory", "reason": "红着合"}),
    ("auto-merge", {"decided_by": "mallory", "enabled": True}),
]

_NEEDS_SEEN_HEAD = {"accept", "merge-anyway", "auto-merge"}


@pytest.mark.parametrize(
    ("route", "body"),
    _CARD_DECISION_ROUTES,
    ids=[route for route, _ in _CARD_DECISION_ROUTES],
)
def test_every_card_decision_route_refuses_a_non_member(client, route, body):
    """One rule, spelled wherever a card is written — not just the route that
    happens to be named in the report.

    `_card_actor` is the single gate all of these share, so a hole in it is a
    hole in all of them; a test that only walked `/accept` would keep passing
    while `/void` or `/auto-merge` stayed open. The stranger's answer is the
    same 403 everywhere, and the card is untouched.
    """
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    if route in _NEEDS_SEEN_HEAD:
        body = {**body, "head_sha": _rendered_head(client, cid)}

    r = client.post(
        f"/accept-cards/{cid}/{route}",
        json=body,
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 403, r.text
    assert "你不是这个频道的成员" in r.json()["message"]

    card = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]
    assert card["status"] == "pending"
    assert card["reviewer_handle"] == "alice"
    assert card["decided_by"] is None
    assert card["approvals"] == []
