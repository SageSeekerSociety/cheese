"""Serving a project's repo over git's own protocol.

This is the direction that was missing: a machine cheese provisions owns its own
tree, so without it the agent's work never reached the topic branch — silently.
"""

import subprocess
import uuid
from pathlib import Path

from app.core.sandbox_auth import mint_scoped_token
from tests.delivery import delivery_task
from tests.machine_work import declare_task


def _project(client) -> str:
    return client.post("/projects", json={"name": "git 项目"}).json()["data"]["id"]


def test_a_project_token_advertises_that_projects_refs(client):
    pid = _project(client)
    token = mint_scoped_token(project_id=pid)
    r = client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": token},
    )
    assert r.status_code == 200
    # git's advertisement always opens with the service pkt-line.
    assert b"# service=git-upload-pack" in r.content


def test_no_token_gets_nothing(client):
    pid = _project(client)
    r = client.get(f"/projects/{pid}/git/info/refs?service=git-upload-pack")
    assert r.status_code == 401, "this endpoint hands out a whole repository"


def test_another_projects_token_is_not_a_key_to_this_one(client):
    mine = _project(client)
    other = _project(client)
    r = client.get(
        f"/projects/{mine}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=other)},
    )
    assert r.status_code == 401


def test_pushing_is_enabled_on_the_repo(client):
    """git-http-backend refuses receive-pack unless the repo opts in, so a push
    would 403 with everything else correct."""
    from app.domain.workspace import service as ws

    pid = _project(client)
    client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    repo: Path = ws.ensure_repo(uuid.UUID(pid))
    value = subprocess.run(
        ["git", "config", "--get", "http.receivepack"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert value == "true"


def test_a_push_reaches_the_files_the_panel_reads(client):
    """A machine's push has to land in the topic's checkout, not just on the ref.

    The file panel, the diff and the sandbox all read files out of that
    checkout, so a ref that moved while the checkout stayed put shows the topic
    exactly as if the push had never happened — which is the failure this whole
    endpoint exists to prevent, and it cannot report itself (`cheese-sync` is a
    Stop hook; raising takes the turn down).
    """
    from app.domain.workspace import service as ws
    from tests.machine_work import machine_commits

    pid = _project(client)
    topic = uuid.uuid4()
    project = uuid.UUID(pid)
    declare_task(project, topic)
    worktree = ws.topic_worktree(project, topic)  # the topic is open
    # Reach the repo the way a machine does, so it is configured as one.
    client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )

    machine_commits(project, topic, {"from_machine.txt": "what the machine wrote\n"})

    assert (worktree / "from_machine.txt").exists()
    assert "from_machine.txt" in [
        f["path"] for f in ws.list_files(project, topic_id=topic)
    ]


def test_a_push_still_lands_after_the_worktree_directory_is_deleted(client):
    """Disk cleanup must not quietly stop a machine from delivering.

    A topic's branch is checked out in a worktree, so git resolves a push
    against that directory. Delete it out of band — an operator reclaiming
    space, a wiped volume — and the branch is still registered to a directory
    that is not there: git fails trying to enter it and rejects every later
    push. The machine cannot report that (`cheese-sync` is a Stop hook), so the
    agent would go on working and delivering nothing at all.
    """
    import shutil

    from app.domain.workspace import service as ws
    from tests.machine_work import machine_commits

    pid = _project(client)
    project, topic = uuid.UUID(pid), uuid.uuid4()
    declare_task(project, topic)
    worktree = ws.topic_worktree(project, topic)
    shutil.rmtree(worktree)

    client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    machine_commits(project, topic, {"delivered.txt": "the work\n"})

    listed = subprocess.run(
        [
            "git",
            "-C",
            str(ws.ensure_repo(project)),
            "ls-tree",
            "--name-only",
            ws.branch_for_task(topic),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "delivered.txt" in listed


# --- 「这批活现在写哪条分支」---------------------------------------------


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
