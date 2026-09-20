"""A machine can take the history without every file version in it.

Everything a machine fetches crosses this backend, and a project's repo is
mostly old file contents: measured on cheese's own repository, a whole clone is
195 MB and the same clone without them 9.6 MB. One 169 MB fetch on dev held the
event loop for 3.7 s on 2026-09-20, which is what the sandbox CLI's
`--filter=blob:none` is for — and git refuses that unless the served repo says
it is allowed.
"""

import subprocess
import uuid
from pathlib import Path

from app.core.sandbox_auth import mint_scoped_token


def _project(client) -> str:
    return client.post("/projects", json={"name": "git 项目"}).json()["data"]["id"]


def test_the_served_repo_allows_a_fetch_without_old_file_contents(client):
    from app.domain.workspace import service as ws

    pid = _project(client)
    # Reach the repo the way a machine does, so it is configured as one.
    client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    repo: Path = ws.ensure_repo(uuid.UUID(pid))

    value = subprocess.run(
        ["git", "config", "--get", "uploadpack.allowFilter"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert value == "true", "without this git answers a filtered fetch with an error"


def test_a_machine_keeps_the_history_it_fetches_that_way(tmp_path, client):
    """The point of taking the commits without the blobs: everything the agent
    reasons with — the log, a diff against the base — still works, and the push
    back still works. Against a local repo here, since what is under test is
    git's behaviour on the shape the CLI asks for, not the HTTP hop."""
    from app.domain.workspace import service as ws

    pid = _project(client)
    client.get(
        f"/projects/{pid}/git/info/refs?service=git-upload-pack",
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid)},
    )
    origin: Path = ws.ensure_repo(uuid.UUID(pid))

    def git(cwd: Path, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    # A branch with a file in it, as a machine's earlier task would have left.
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "--quiet", str(origin), "seed")
    (seed / "kept.txt").write_text("first\n")
    git(seed, "add", "kept.txt")
    git(seed, "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "first")
    git(seed, "push", "--quiet", "origin", "HEAD:refs/heads/task/thin")

    cache = tmp_path / "cache.git"
    git(tmp_path, "init", "--quiet", "--bare", str(cache))
    git(cache, "remote", "add", "origin", str(origin))
    git(
        cache,
        "fetch",
        "--quiet",
        "--filter=blob:none",
        "origin",
        "refs/heads/task/thin:refs/remotes/origin/task/thin",
    )

    work = tmp_path / "work"
    git(cache, "worktree", "add", "--quiet", str(work), "refs/remotes/origin/task/thin")
    assert (work / "kept.txt").read_text() == "first\n", "the checkout has its files"
    assert git(work, "log", "--oneline") != "", "the history came with it"

    (work / "kept.txt").write_text("first\nagent\n")
    git(work, "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qam", "agent")
    git(work, "push", "--quiet", "origin", "HEAD:refs/heads/task/thin")

    assert "agent" in git(origin, "log", "--oneline", "task/thin")
