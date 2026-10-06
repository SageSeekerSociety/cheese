"""An executor receives task metadata without a backend workspace descriptor."""

import asyncio
import uuid
from pathlib import Path

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import open_task, post_project, session_auth_headers


def test_task_metadata_does_not_create_a_backend_workspace_descriptor(client):
    client.headers.update(session_auth_headers("alice"))
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    room = client.post("/topics", json={"project_id": project, "title": "room"}).json()[
        "data"
    ]["id"]
    task = open_task(client, room, "work")
    descriptors = Path(settings.workspace_root) / ".task-workspaces"
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


def test_task_metadata_names_the_pr_while_it_is_in_the_merge_queue(client):
    from app.domain.review.models import AcceptCard, AcceptStatus

    client.headers.update(session_auth_headers("alice"))
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    room = client.post("/topics", json={"project_id": project, "title": "room"}).json()[
        "data"
    ]["id"]
    task = open_task(client, room, "work")["id"]
    token = mint_scoped_token(project_id=project, topic_id=room)

    def queued_pr():
        response = client.get(
            f"/projects/{project}/git/tasks/{task}", headers={"X-Cheese-Token": token}
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]["merge_queued_pr"]

    assert queued_pr() is None

    async def queue() -> None:
        async with client.test_factory() as s:
            s.add(
                AcceptCard(
                    topic_id=uuid.UUID(room),
                    task_id=uuid.UUID(task),
                    reviewer_handle="alice",
                    status=AcceptStatus.pending,
                    pr_number=42,
                    decided_by="alice",
                    note_code="waiting_merge_queue",
                )
            )
            await s.commit()

    asyncio.run(queue())
    assert queued_pr() == 42
