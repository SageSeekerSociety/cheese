"""Integration tests for the Space + Task domain (REST CRUD).

Exercises the full chain space -> template -> task, listing each, and the
404 paths, through the FastAPI TestClient with an in-memory SQLite DB.
"""

import uuid


def test_create_and_get_space(client):
    r = client.post(
        "/api/spaces",
        json={"name": "信院", "kind": "school", "description": "School of Info"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 200
    space = body["data"]
    assert space["name"] == "信院"
    assert space["kind"] == "school"
    assert space["description"] == "School of Info"
    space_id = space["id"]

    r = client.get(f"/api/spaces/{space_id}")
    assert r.json()["code"] == 200
    assert r.json()["data"]["id"] == space_id


def test_create_space_defaults_kind_other(client):
    r = client.post("/api/spaces", json={"name": "书院"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["kind"] == "other"
    assert data["description"] == ""


def test_list_spaces(client):
    client.post("/api/spaces", json={"name": "A"})
    client.post("/api/spaces", json={"name": "B"})

    r = client.get("/api/spaces")
    body = r.json()
    assert body["code"] == 200
    assert body["data"]["total"] == 2
    assert len(body["data"]["data"]) == 2


def test_get_space_404(client):
    r = client.get(f"/api/spaces/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["code"] == 404
    assert r.json()["data"] is None


def test_full_chain_space_template_task(client):
    # Space
    space_id = client.post("/api/spaces", json={"name": "创研课"}).json()["data"]["id"]

    # Template under space
    r = client.post(
        f"/api/spaces/{space_id}/templates",
        json={
            "name": "创研课 2026 秋",
            "description": "protocol",
            "resource_pack": {"gpu_hours": 100},
            "conditions": [{"required_topic": "结题答辩"}],
            "default_role": "mentor",
        },
    )
    assert r.json()["code"] == 200
    template = r.json()["data"]
    assert template["space_id"] == space_id
    assert template["resource_pack"] == {"gpu_hours": 100}
    assert template["conditions"] == [{"required_topic": "结题答辩"}]
    assert template["default_role"] == "mentor"
    template_id = template["id"]

    # Get template
    r = client.get(f"/api/templates/{template_id}")
    assert r.json()["code"] == 200
    assert r.json()["data"]["id"] == template_id

    # List templates for space
    r = client.get(f"/api/spaces/{space_id}/templates")
    assert r.json()["data"]["total"] == 1
    assert len(r.json()["data"]["data"]) == 1

    # Task under template
    r = client.post(
        f"/api/templates/{template_id}/tasks",
        json={"title": "用 AI 做点啥", "description": "go"},
    )
    assert r.json()["code"] == 200
    task = r.json()["data"]
    assert task["template_id"] == template_id
    assert task["title"] == "用 AI 做点啥"
    task_id = task["id"]

    # Get task
    r = client.get(f"/api/tasks/{task_id}")
    assert r.json()["code"] == 200
    assert r.json()["data"]["id"] == task_id

    # List tasks for template
    r = client.get(f"/api/templates/{template_id}/tasks")
    assert r.json()["data"]["total"] == 1
    assert len(r.json()["data"]["data"]) == 1


def test_template_defaults(client):
    space_id = client.post("/api/spaces", json={"name": "S"}).json()["data"]["id"]
    r = client.post(f"/api/spaces/{space_id}/templates", json={"name": "T"})
    data = r.json()["data"]
    assert data["resource_pack"] == {}
    assert data["conditions"] == []
    assert data["default_role"] is None
    assert data["description"] == ""


def test_create_template_for_missing_space_404(client):
    r = client.post(f"/api/spaces/{uuid.uuid4()}/templates", json={"name": "T"})
    assert r.status_code == 404
    assert r.json()["code"] == 404


def test_list_templates_for_missing_space_404(client):
    r = client.get(f"/api/spaces/{uuid.uuid4()}/templates")
    assert r.status_code == 404


def test_get_template_404(client):
    r = client.get(f"/api/templates/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["data"] is None


def test_create_task_for_missing_template_404(client):
    r = client.post(f"/api/templates/{uuid.uuid4()}/tasks", json={"title": "x"})
    assert r.status_code == 404


def test_list_tasks_for_missing_template_404(client):
    r = client.get(f"/api/templates/{uuid.uuid4()}/tasks")
    assert r.status_code == 404


def test_get_task_404(client):
    r = client.get(f"/api/tasks/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["data"] is None
