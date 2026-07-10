"""Integration tests for the machine quality gate (spec §4.4/§9, eval C2).

When a project configures `check_command`, filing an accept card runs that
command in the topic's workspace BEFORE the card reaches a reviewer:
green → card becomes pending (gate_passed_at + output recorded);
red → card becomes gate_failed and 芝士 gets a system nudge to fix it.
No check_command configured → the card is born pending (现行为, backward compat).
"""

import time
import uuid

import pytest

from app.domain.review import gate
from tests.conftest import wait_turns_idle


@pytest.fixture(autouse=True)
def _gate_logs(tmp_path, monkeypatch):
    """Keep gate logs out of the repo's logs/ during tests."""
    monkeypatch.setattr(gate, "LOG_DIR", tmp_path / "gate-logs")


def _make_project(client) -> str:
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
        json={"reviewer_handle": reviewer, "routing_reason": "最懂"},
    )
    assert r.status_code == 200
    return r.json()["data"]


def _latest_card(client, topic_id: str) -> dict:
    cards = client.get(f"/api/topics/{topic_id}/accept-card").json()["data"]["data"]
    assert cards
    return cards[0]


def _wait_gate_settled(client, topic_id: str, timeout: float = 20.0) -> dict:
    """Poll until the background gate finishes (card leaves pending_gate)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        card = _latest_card(client, topic_id)
        if card["status"] != "pending_gate":
            return card
        time.sleep(0.05)
    raise AssertionError("gate never settled (card stuck in pending_gate)")


def test_no_check_command_card_born_pending(client):
    # Backward compat: an unconfigured project keeps the old behavior exactly.
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    card = _file_card(client, tid)
    assert card["status"] == "pending"
    assert card["gate_passed_at"] is None
    assert card["gate_output"] == ""


def test_quality_gate_settings_roundtrip(client):
    pid = _make_project(client)
    r = client.get(f"/api/projects/{pid}/quality-gate")
    assert r.json()["data"] == {"check_command": "", "approvals_required": 1}

    _set_gate(client, pid, "echo hi")
    r = client.put(
        f"/api/projects/{pid}/quality-gate", json={"approvals_required": 3}
    )
    assert r.status_code == 200
    r = client.get(f"/api/projects/{pid}/quality-gate")
    assert r.json()["data"] == {"check_command": "echo hi", "approvals_required": 3}

    # Clearing the command removes the gate; approvals stay.
    r = client.put(f"/api/projects/{pid}/quality-gate", json={"check_command": ""})
    assert r.status_code == 200
    r = client.get(f"/api/projects/{pid}/quality-gate")
    assert r.json()["data"] == {"check_command": "", "approvals_required": 3}


def test_quality_gate_rejects_bad_approvals(client):
    pid = _make_project(client)
    r = client.put(
        f"/api/projects/{pid}/quality-gate", json={"approvals_required": 0}
    )
    assert r.status_code == 422


def test_gate_green_promotes_card_to_pending(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _set_gate(client, pid, "echo all-good")

    card = _file_card(client, tid)
    assert card["status"] == "pending_gate"

    settled = _wait_gate_settled(client, tid)
    assert settled["status"] == "pending"
    assert settled["gate_passed_at"] is not None
    assert "all-good" in settled["gate_output"]

    # Full output landed in the gate log file.
    topic8 = uuid.UUID(tid).hex[:8]
    log = gate.LOG_DIR / f"gate-{topic8}.log"
    assert log.is_file()
    assert "all-good" in log.read_text(encoding="utf-8")

    # A green-gated card is acceptable as usual.
    r = client.post(
        f"/api/accept-cards/{settled['id']}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"


def test_gate_red_fails_card_and_nudges_cheese(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    _set_gate(client, pid, "echo boom-details >&2; exit 3")

    card = _file_card(client, tid)
    assert card["status"] == "pending_gate"

    settled = _wait_gate_settled(client, tid)
    assert settled["status"] == "gate_failed"
    assert settled["gate_passed_at"] is None
    assert "boom-details" in settled["gate_output"]

    # A failed card cannot be accepted…
    r = client.post(
        f"/api/accept-cards/{settled['id']}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 422
    # …and cannot be rejected either (it never reached the reviewer).
    r = client.post(
        f"/api/accept-cards/{settled['id']}/reject", json={"decided_by": "alice"}
    )
    assert r.status_code == 422

    # 芝士 got a system nudge to go fix the failure. The nudge turn is
    # submitted right after the card settles — poll for it, then drain.
    contents = ""
    deadline = time.time() + 10
    while time.time() < deadline:
        wait_turns_idle()
        blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
        contents = "\n".join(b.get("content") or "" for b in blocks)
        if "boom-details" in contents:
            break
        time.sleep(0.05)
    assert "检查" in contents
    assert "boom-details" in contents

    # gate_failed is not in-flight: 芝士 can file a fresh card after fixing.
    _set_gate(client, pid, "echo fixed-now")
    fresh = _file_card(client, tid, "bob")
    assert fresh["status"] == "pending_gate"
    assert _wait_gate_settled(client, tid)["status"] == "pending"


def test_pending_gate_blocks_accept_and_second_card(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    # Slow enough that the assertions below run while the gate is in flight.
    _set_gate(client, pid, "sleep 2; echo ok")

    card = _file_card(client, tid)
    assert card["status"] == "pending_gate"

    # Can't accept while the platform is still checking.
    r = client.post(
        f"/api/accept-cards/{card['id']}/accept", json={"decided_by": "alice"}
    )
    assert r.status_code == 422

    # One in-flight card per topic: a second one is rejected.
    r = client.post(
        f"/api/topics/{tid}/accept-card",
        json={"reviewer_handle": "bob", "routing_reason": "x"},
    )
    assert r.status_code == 422

    assert _wait_gate_settled(client, tid)["status"] == "pending"
