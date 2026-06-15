"""Aggregation endpoints — project overview + Space board (evals F3/G2)."""


def test_project_overview(client):
    p = client.post(
        "/api/projects", json={"name": "P", "owner_handle": "user-1"}
    ).json()["data"]
    pid = p["id"]
    client.post(f"/api/projects/{pid}/members", json={"user_handle": "user-1"})
    client.post(
        f"/api/projects/{pid}/milestones",
        json={"title": "中期", "due_date": "2030-01-01T00:00:00Z"},
    )
    # A decision request addressed to user-1 → should appear in 等你处理的事.
    client.post(
        f"/api/projects/{pid}/notifications",
        json={
            "level": "light",
            "kind": "decision_request",
            "title": "选哪个方案",
            "target_handle": "user-1",
        },
    )

    ov = client.get(f"/api/projects/{pid}/overview").json()["data"]
    assert ov["name"] == "P"
    assert ov["topic_count"] >= 1  # root topic auto-created
    assert ov["next_milestone"]["title"] == "中期"
    assert "user-1" in ov["waiting_on_you"]
    assert any(m["handle"] == "user-1" for m in ov["members"])


def test_space_board_lists_linked_teams(client):
    # Build Space → template → task, then link a project.
    space = client.post("/api/spaces", json={"name": "明理书院"}).json()["data"]
    tmpl = client.post(
        f"/api/spaces/{space['id']}/templates", json={"name": "入驻"}
    ).json()["data"]
    task = client.post(
        f"/api/templates/{tmpl['id']}/tasks", json={"title": "题目"}
    ).json()["data"]
    p = client.post("/api/projects", json={"name": "队伍A"}).json()["data"]
    client.post(f"/api/projects/{p['id']}/tasks", json={"task_id": task["id"]})

    board = client.get(f"/api/spaces/{space['id']}/dashboard").json()["data"]
    assert board["total"] == 1
    assert board["teams"][0]["name"] == "队伍A"


def test_space_board_empty_for_space_without_links(client):
    space = client.post("/api/spaces", json={"name": "空书院"}).json()["data"]
    board = client.get(f"/api/spaces/{space['id']}/dashboard").json()["data"]
    assert board["total"] == 0


def test_overview_404_for_missing_project(client):
    r = client.get("/api/projects/00000000-0000-0000-0000-000000000000/overview")
    assert r.status_code == 404
