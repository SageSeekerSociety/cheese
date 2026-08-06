"""Scheduler — 定期巡检 tick across projects (spec §9.1)."""


def test_scheduler_tick_inspects_projects(client):
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    root_id = p["root_topic_id"]

    r = client.post("/api/admin/scheduler/tick")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["projects_inspected"] >= 1
    assert data["errors"] == []

    # Each inspected project gets a heartbeat decision log in its root topic.
    blocks = client.get(f"/api/topics/{root_id}/blocks").json()["data"]["data"]
    assert any("巡检决策日志" in b["content"] for b in blocks)
