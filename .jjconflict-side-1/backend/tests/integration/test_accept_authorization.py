"""Security regression tests: accept-card decision endpoints must not let an
unauthenticated caller, or an authenticated caller who isn't the routed
reviewer, decide someone else's card (accept/reject/revoke/reassign/approve).

Before the fix, `decided_by`/`approver_handle` were trusted straight from the
request body with no authentication at all — anyone who could reach the API
could accept/reject/revoke any card by simply naming the right handle.
"""

from tests.integration.conftest import session_auth_headers


def _make_project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_card(client, topic_id: str, reviewer: str = "alice") -> str:
    r = client.post(
        f"/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def test_accept_without_auth_401(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    # No Authorization header at all — a fully anonymous caller.
    r = client.post(f"/accept-cards/{cid}/accept", json={"decided_by": "alice"})
    assert r.status_code == 401

    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"


def test_accept_by_non_reviewer_403_even_with_matching_body_field(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    # An authenticated attacker (mallory) tries to forge the body's decided_by
    # as "alice" — the resolved actor identity (mallory, from the verified
    # token) must win over the self-reported body field.
    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 403
    assert "验收人" in r.json()["message"]

    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"
    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


def test_accept_by_routed_reviewer_succeeds(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    r = client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
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

    r = client.post(
        f"/accept-cards/{cid}/reject",
        json={"decided_by": "mallory"},
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 403
    cards = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"]
    assert cards[0]["status"] == "pending"


def test_revoke_without_auth_401(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")
    client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )

    r = client.post(f"/accept-cards/{cid}/revoke", json={"decided_by": "alice"})
    assert r.status_code == 401
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_by"] == "alice"


def test_revoke_ignores_spoofed_body_identity(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")
    client.post(
        f"/accept-cards/{cid}/accept",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )

    # mallory authenticates as herself but claims to be "alice" in the body —
    # the allow-list check must use the verified handle, not the body.
    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("mallory"),
    )
    assert r.status_code == 422
    assert client.get(f"/topics/{tid}").json()["data"]["accepted_by"] == "alice"


def test_reassign_without_auth_401(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    cid = _make_card(client, tid, "alice")

    r = client.post(
        f"/accept-cards/{cid}/reassign",
        json={"reviewer_handle": "mallory", "routing_reason": "x"},
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
    r = client.post(
        f"/accept-cards/{cid}/approve",
        json={"approver_handle": "alice"},
        headers=session_auth_headers("bob"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["approvals"] == ["bob"]
