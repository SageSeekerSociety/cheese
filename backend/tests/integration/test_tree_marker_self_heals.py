"""Task descriptors are restored from persisted ownership after authorization."""

import uuid
from pathlib import Path

from app.domain.workspace import service as ws
from tests.integration.conftest import session_auth_headers


def test_missing_task_descriptor_is_restored_by_a_task_file_read(client):
    client.headers.update(session_auth_headers("alice"))
    project = client.post("/projects", json={"name": "P"}).json()["data"]["id"]
    room = client.post("/topics", json={"project_id": project, "title": "room"}).json()[
        "data"
    ]["id"]
    task = client.post(
        f"/topics/{room}/split", json={"title": "work", "reviewer_handle": "alice"}
    ).json()["data"]
    descriptor = (
        Path(ws.settings.workspace_root)
        / ".task-workspaces"
        / f"{uuid.UUID(task['id']).hex}.json"
    )
    before = descriptor.read_bytes()
    descriptor.unlink()
    response = client.get(
        f"/projects/{project}/files", params={"topic": room, "task": task["id"]}
    )
    assert response.status_code == 200, response.text
    assert descriptor.read_bytes() == before
    assert not (
        Path(ws.settings.workspace_root)
        / ".task-workspaces"
        / f"{uuid.UUID(room).hex}.json"
    ).exists()
