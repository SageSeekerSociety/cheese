"""An executor receives task metadata without a backend workspace descriptor."""

from pathlib import Path

from app.core.sandbox_auth import mint_scoped_token
from app.domain.workspace import service as ws
from tests.integration.conftest import session_auth_headers


def test_task_metadata_does_not_create_a_backend_workspace_descriptor(client):
    client.headers.update(session_auth_headers("alice"))
    project = client.post("/projects", json={"name": "P"}).json()["data"]["id"]
    room = client.post("/topics", json={"project_id": project, "title": "room"}).json()[
        "data"
    ]["id"]
    task = client.post(
        f"/topics/{room}/split", json={"title": "work", "reviewer_handle": "alice"}
    ).json()["data"]
    descriptors = Path(ws.settings.workspace_root) / ".task-workspaces"
    assert not descriptors.exists()
    response = client.get(
        f"/projects/{project}/git/tasks/{task['id']}",
        headers={
            "X-Cheese-Token": mint_scoped_token(project_id=project, topic_id=room)
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["branch"] == task["branch_name"]
    assert not descriptors.exists()
