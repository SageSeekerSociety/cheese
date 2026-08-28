"""Serving a project's repo over git's own protocol.

This is the direction that was missing: a machine cheese provisions owns its own
tree, so without it the agent's work never reached the topic branch — silently.
"""

import subprocess
import uuid
from pathlib import Path

from app.core.sandbox_auth import mint_scoped_token


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
