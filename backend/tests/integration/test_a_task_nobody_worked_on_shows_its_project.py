"""A task nobody has worked in yet shows its project's files: what its first
checkout would start from, not an empty list."""

import uuid

from tests.delivery import delivery_task
from tests.integration.conftest import post_project, session_auth_headers
from tests.support import git_store


def test_a_task_never_worked_shows_the_files_its_project_has(client):
    project = uuid.UUID(
        post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    )
    repo = git_store.ensure_repo(project)
    (repo / "README.md").write_text("the project\n")
    git_store.git(repo, "add", "README.md")
    git_store.git(repo, "commit", "-qm", "Add the readme")
    room = client.post(
        "/topics",
        json={"project_id": str(project), "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    task = delivery_task(client, room, commit=False)

    listed = client.get(
        f"/projects/{project}/files",
        params={"task": str(task.id), "source": "live"},
        headers=session_auth_headers("alice"),
    )

    assert listed.status_code == 200, listed.text
    paths = [entry["path"] for entry in listed.json()["data"]["data"]]
    assert "README.md" in paths
    read = client.get(
        f"/projects/{project}/file",
        params={"task": str(task.id), "source": "live", "path": "README.md"},
        headers=session_auth_headers("alice"),
    )
    assert read.status_code == 200, read.text
    assert read.json()["data"]["content"] == "the project\n"
