"""GET /api/topics/{id}/status — the 盲飞防护 snapshot (`cheese status`)."""

import uuid


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


def test_status_snapshot_shape(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    r = client.get(f"/api/topics/{tid}/status")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["topic"]["id"] == tid
    assert data["topic"]["title"] == "做一个东西"
    assert data["cards"] == []
    # No turn has run for this topic (or the ring buffer rolled) → null.
    assert data["turn"] is None
    plat = data["platform"]
    assert plat["credits"]["unlimited"] is True
    assert "active_turns" in plat
    assert "queued_turns" in plat
    assert "disk" in plat


def test_status_includes_cards_with_gate_tail(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    r = client.post(
        f"/api/topics/{tid}/accept-card",
        json={"reviewer_handle": "alice", "routing_reason": "最懂"},
    )
    assert r.status_code == 200

    r = client.get(f"/api/topics/{tid}/status")
    cards = r.json()["data"]["cards"]
    assert len(cards) == 1
    assert cards[0]["status"] == "pending"
    assert cards[0]["reviewer"] == "alice"
    assert "gate_output_tail" in cards[0]


def test_status_404_for_missing_topic(client):
    r = client.get(f"/api/topics/{uuid.uuid4()}/status")
    assert r.status_code == 404
