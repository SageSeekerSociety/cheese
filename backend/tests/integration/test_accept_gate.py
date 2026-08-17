"""The machine quality gate is RETIRED (docs/accept-is-merge.md #296, stage 1).

Filing an accept card used to run a project's `check_command` in the topic
workspace BEFORE the card reached a reviewer (born `pending_gate`, green →
`pending`, red → `gate_failed`). That whole mechanism is gone: a card is the
platform's view of a PR, and real CI on that PR — not a private platform check —
is what decides whether a change is good.

These tests pin the retirement as observable behaviour: a project WITH a
`check_command` still configured files a card that is born `pending` (never
`pending_gate`), no platform check runs, and the card is immediately usable —
plus the settings endpoint that stores `check_command` / `approvals_required`
still round-trips (a later stage clears `check_command`; this one only stops
consuming it).
"""

from tests.integration.conftest import session_auth_headers


def _authed(client):
    client.headers.update(session_auth_headers("alice"))


def _make_project(client) -> str:
    _authed(client)
    r = client.post("/api/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str) -> str:
    r = client.post(
        "/api/topics", json={"project_id": project_id, "title": "做一个东西"}
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _set_gate(client, project_id: str, command: str) -> None:
    r = client.put(
        f"/api/projects/{project_id}/quality-gate",
        json={"check_command": command},
    )
    assert r.status_code == 200


def _file_card(client, topic_id: str, reviewer: str = "alice") -> dict:
    r = client.post(
        f"/api/topics/{topic_id}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200
    return r.json()["data"]


def _latest_card(client, topic_id: str) -> dict:
    cards = client.get(f"/api/topics/{topic_id}/accept-card").json()["data"]["data"]
    assert cards
    return cards[0]


# --------------------------------------------------------------------------
# The gate is unreachable — a configured check_command no longer gates
# --------------------------------------------------------------------------


def test_no_check_command_card_born_pending(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _file_card(client, tid)
    assert card["status"] == "pending"
    assert card["gate_passed_at"] is None
    assert card["gate_output"] == ""


def test_a_configured_check_command_no_longer_produces_a_pending_gate_card(client):
    """The retirement guard (like #302's parked-bypass guard): a project can
    still have a `check_command` stored, but filing a card must NOT reach
    `pending_gate` any more — the card is born `pending` and no platform check
    runs against it."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    # A command that WOULD have failed the old gate (exit 3): proof the platform
    # is not running it — if it did, the card would not be `pending`.
    _set_gate(client, pid, "echo boom-details >&2; exit 3")

    card = _file_card(client, tid)
    assert card["status"] == "pending"
    assert card["status"] != "pending_gate"
    assert card["gate_passed_at"] is None
    assert card["gate_output"] == ""

    # The card is directly usable — no gate to wait on, no green to earn from
    # the platform.
    assert _latest_card(client, tid)["status"] == "pending"


def test_a_pending_card_with_a_check_command_is_acceptable_immediately(client):
    """No gate stands between filing and accepting: a card on a project that
    still carries a `check_command` accepts straight away (local merge, since no
    GitHub App is configured in the test env)."""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _set_gate(client, pid, "echo all-good")

    card = _file_card(client, tid)
    assert card["status"] == "pending"

    r = client.post(
        f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "accepted"


def test_second_card_still_blocked_while_first_is_pending(client):
    """One live card per topic — unchanged. (Used to also cover pending_gate;
    that state is no longer reachable, so a plain pending card carries it.)"""
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _set_gate(client, pid, "echo all-good")

    first = _file_card(client, tid)
    assert first["status"] == "pending"

    r = client.post(
        f"/api/topics/{tid}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "bob",
            "routing_reason": "x",
        },
    )
    assert r.status_code == 422
    assert "改验收人" in r.json()["message"]


# --------------------------------------------------------------------------
# The quality-gate settings endpoint still stores/round-trips its values
# --------------------------------------------------------------------------


def test_quality_gate_settings_roundtrip(client):
    pid = _make_project(client)
    r = client.get(f"/api/projects/{pid}/quality-gate")
    assert r.json()["data"] == {"check_command": "", "approvals_required": 1}

    _set_gate(client, pid, "echo hi")
    r = client.put(f"/api/projects/{pid}/quality-gate", json={"approvals_required": 3})
    assert r.status_code == 200
    r = client.get(f"/api/projects/{pid}/quality-gate")
    assert r.json()["data"] == {"check_command": "echo hi", "approvals_required": 3}

    # Clearing the command removes it; approvals stay.
    r = client.put(f"/api/projects/{pid}/quality-gate", json={"check_command": ""})
    assert r.status_code == 200
    r = client.get(f"/api/projects/{pid}/quality-gate")
    assert r.json()["data"] == {"check_command": "", "approvals_required": 3}


def test_quality_gate_rejects_bad_approvals(client):
    pid = _make_project(client)
    r = client.put(f"/api/projects/{pid}/quality-gate", json={"approvals_required": 0})
    assert r.status_code == 422


def test_quality_gate_update_requires_human_project_admin(client):
    pid = _make_project(client)

    client.headers.pop("Authorization")
    r = client.put(
        f"/api/projects/{pid}/quality-gate", json={"check_command": "echo bad"}
    )
    assert r.status_code == 404

    client.headers.update(session_auth_headers("mallory"))
    r = client.put(
        f"/api/projects/{pid}/quality-gate", json={"check_command": "echo bad"}
    )
    assert r.status_code == 404


def test_quality_gate_update_allows_project_lead_not_ordinary_member(client):
    pid = _make_project(client)
    for handle, role in (("lead-user", "lead"), ("member-user", "member")):
        r = client.post(
            f"/api/projects/{pid}/members",
            json={"user_handle": handle, "role": role},
        )
        assert r.status_code == 200

    client.headers.update(session_auth_headers("lead-user"))
    r = client.put(
        f"/api/projects/{pid}/quality-gate", json={"check_command": "echo safe"}
    )
    assert r.status_code == 200

    client.headers.update(session_auth_headers("member-user"))
    r = client.put(
        f"/api/projects/{pid}/quality-gate", json={"check_command": "echo bad"}
    )
    assert r.status_code == 404


def test_quality_gate_rejects_oversized_or_nul_command(client):
    pid = _make_project(client)
    r = client.put(
        f"/api/projects/{pid}/quality-gate", json={"check_command": "x" * 4097}
    )
    assert r.status_code == 422
    r = client.put(
        f"/api/projects/{pid}/quality-gate", json={"check_command": "echo\x00bad"}
    )
    assert r.status_code == 422


def test_an_accepted_card_still_cannot_be_rejected(client):
    # Widening reject must not turn it into a way around the terminal states.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _set_gate(client, pid, "echo all-good")

    card = _file_card(client, tid)
    assert card["status"] == "pending"

    r = client.post(
        f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 200, r.text

    r = client.post(
        f"/api/accept-cards/{card['id']}/reject",
        json={"decided_by": "alice", "note": "x"},
    )
    assert r.status_code == 422
    assert "已处理" in r.text
