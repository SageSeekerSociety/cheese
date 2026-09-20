"""Task metadata remains on the backend; native Git is served by the forge."""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from tests.delivery import delivery_task


def _project(client) -> str:
    return client.post("/projects", json={"name": "git 项目"}).json()["data"]["id"]


def test_backend_git_protocol_endpoint_is_retired(client):
    pid = _project(client)
    response = client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    assert response.status_code == 404


def test_task_manifest_names_only_that_tasks_branch_and_target(client):
    project = client.post("/projects", json={"name": "Task manifest"}).json()["data"]
    task = delivery_task(client, project["root_topic_id"], commit=False)
    headers = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project["id"], topic_id=project["root_topic_id"]
        )
    }
    response = client.get(
        f"/projects/{project['id']}/git/tasks/{task.id}", headers=headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["task_id"] == str(task.id)
    assert data["room_id"] == project["root_topic_id"]
    assert data["branch"] == task.branch_name
    assert data["base"] == task.base_branch
    assert data["closed"] is False


def test_manifest_refuses_an_unknown_task(client):
    pid = _project(client)
    response = client.get(
        f"/projects/{pid}/git/tasks/{uuid.uuid4()}",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    assert response.status_code == 404


def test_room_branch_negotiation_endpoint_is_retired(client):
    pid = _project(client)
    response = client.post(
        f"/projects/{pid}/git/branch/{uuid.uuid4()}",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    assert response.status_code == 404
