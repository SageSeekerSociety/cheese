"""GET /api/topics/{id}/status — the 盲飞防护 snapshot (`cheese status`)."""

import uuid

from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import join_project_team, post_project


def _make_project(client) -> str:
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    pid = r.json()["data"]["id"]
    # 2026-09-27: 递卡那道门现在先问「这个人在不在房间里」
    # (`_require_reviewer_in_room`)。这里的卡递给 alice，就让她像真实参与者一样进
    # 项目 —— 要检验的是 status 快照带上卡与闸门输出，不是名册。
    join_project_team(client, pid, "alice")
    return pid


def _make_topic(client, project_id: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": "做一个东西"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def test_status_snapshot_shape(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)

    r = client.get(f"/topics/{tid}/status")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["topic"]["id"] == tid
    assert data["topic"]["title"] == "做一个东西"
    assert data["cards"] == []
    # No turn has run for this topic (or the ring buffer rolled) → null.
    assert data["turn"] is None
    plat = data["platform"]
    assert isinstance(plat["credits"]["unlimited"], bool)
    assert "active_turns" in plat
    assert "queued_turns" in plat
    assert "disk" in plat


def test_status_includes_cards_with_gate_tail(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid)
    r = client.post(
        f"/topics/{delivery_task_id(client, tid)}/accept-card",
        headers=delivery_headers(client, tid),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
    )
    assert r.status_code == 200

    r = client.get(f"/topics/{tid}/status")
    cards = r.json()["data"]["cards"]
    assert len(cards) == 1
    assert cards[0]["status"] == "pending"
    assert cards[0]["reviewer"] == "alice"
    assert "gate_output_tail" in cards[0]


def test_status_404_for_missing_topic(client):
    r = client.get(f"/topics/{uuid.uuid4()}/status")
    assert r.status_code == 404
