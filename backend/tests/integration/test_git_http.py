"""Serving a project's repo over git's own protocol.

This is the direction that was missing: a machine cheese provisions owns its own
tree, so without it the agent's work never reached the topic branch — silently.
"""

import subprocess
import uuid
from pathlib import Path

from app.core.sandbox_auth import mint_scoped_token


def _project(client) -> str:
    return client.post("/api/projects", json={"name": "git 项目"}).json()["data"]["id"]


def test_a_project_token_advertises_that_projects_refs(client):
    pid = _project(client)
    token = mint_scoped_token(project_id=pid)
    r = client.get(
        f"/api/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": token},
    )
    assert r.status_code == 200
    # git's advertisement always opens with the service pkt-line.
    assert b"# service=git-upload-pack" in r.content


def test_no_token_gets_nothing(client):
    pid = _project(client)
    r = client.get(f"/api/projects/{pid}/git/info/refs?service=git-upload-pack")
    assert r.status_code == 401, "this endpoint hands out a whole repository"


def test_another_projects_token_is_not_a_key_to_this_one(client):
    mine = _project(client)
    other = _project(client)
    r = client.get(
        f"/api/projects/{mine}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=other)},
    )
    assert r.status_code == 401


def test_pushing_is_enabled_on_the_repo(client):
    """git-http-backend refuses receive-pack unless the repo opts in, so a push
    would 403 with everything else correct."""
    from app.domain.workspace import service as ws

    pid = _project(client)
    client.get(
        f"/api/projects/{pid}/git/info/refs?service=git-upload-pack",
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


def test_a_push_keeps_jj_and_git_from_diverging(client, monkeypatch):
    """jj is colocated on these repos and does not see a push on its own.

    That is not cosmetic: snapshot_worktree moves the topic bookmark with
    --allow-backwards, so a jj view still pointing at the old commit could drag
    the branch back over work the machine just pushed.
    """
    import app.api.routes.git_http as git_http

    calls: list[list[str]] = []
    real = git_http.subprocess.run

    def spy(cmd, *a, **kw):
        calls.append(list(cmd))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(git_http.subprocess, "run", spy)
    pid = _project(client)
    client.post(
        f"/api/projects/{pid}/git/git-receive-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
        content=b"0000",
    )
    assert any(c[:3] == ["jj", "git", "import"] for c in calls)
