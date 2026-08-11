"""Integration tests for the Task Template market on top of a Space.

The cheesex uuid Space CRUD stub (POST/GET /api/spaces) was retired in the
fusion merge (unify P1b/c: one Space = 知是's int Space, served at main's
/spaces with a different shape). What survives — and what these tests cover — is
the task-template + task chain that lives on top of a Space:
space -> template -> task, plus the 404 paths. The Space itself is seeded
directly (see tests.conftest.seed_space).
"""

import uuid

from tests.conftest import seed_space

# A Space id that does not exist (int PKs start at 1).
MISSING_SPACE_ID = 999_999_999


def test_full_chain_space_template_task(client):
    space_id = seed_space(client, "创研课")

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
    space_id = seed_space(client, "S")
    r = client.post(f"/api/spaces/{space_id}/templates", json={"name": "T"})
    data = r.json()["data"]
    assert data["resource_pack"] == {}
    assert data["conditions"] == []
    assert data["default_role"] is None
    assert data["description"] == ""


def test_create_template_for_missing_space_404(client):
    r = client.post(f"/api/spaces/{MISSING_SPACE_ID}/templates", json={"name": "T"})
    assert r.status_code == 404
    assert r.json()["code"] == 404


def test_list_templates_for_missing_space_404(client):
    r = client.get(f"/api/spaces/{MISSING_SPACE_ID}/templates")
    assert r.status_code == 404


def test_get_template_404(client):
    r = client.get(f"/api/templates/{uuid.uuid4()}")
    assert r.status_code == 404
    # Merged error envelope nests data under ``error`` (no top-level ``data``).
    assert r.json()["error"]["data"] is None


def test_create_task_for_missing_template_404(client):
    r = client.post(f"/api/templates/{uuid.uuid4()}/tasks", json={"title": "x"})
    assert r.status_code == 404


def test_list_tasks_for_missing_template_404(client):
    r = client.get(f"/api/templates/{uuid.uuid4()}/tasks")
    assert r.status_code == 404


def test_get_task_404(client):
    r = client.get(f"/api/tasks/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["data"] is None
