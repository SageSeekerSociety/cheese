"""定期巡检 / heartbeat (eval G1) — with the stub agent."""


def test_heartbeat_runs_and_logs_decision(client):
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    pid = p["id"]
    root_id = p["root_topic_id"]

    r = client.post(f"/api/projects/{pid}/heartbeat")
    assert r.status_code == 200
    assert "decision_log" in r.json()["data"]

    # The decision log is recorded in the root topic (施工现场 "为什么催").
    blocks = client.get(f"/api/topics/{root_id}/blocks").json()["data"]["data"]
    assert any(b["kind"] == "event" and "巡检决策日志" in b["content"] for b in blocks)


def test_heartbeat_404_for_missing_project(client):
    r = client.post("/api/projects/00000000-0000-0000-0000-000000000000/heartbeat")
    assert r.status_code == 404
