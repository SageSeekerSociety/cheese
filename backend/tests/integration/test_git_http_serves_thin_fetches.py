"""A filtered fetch retains the history and supports subsequent pushes."""

import subprocess
import uuid
from pathlib import Path


def _project(client) -> str:
    return client.post("/projects", json={"name": "git 项目"}).json()["data"]["id"]


def test_a_machine_keeps_the_history_it_fetches_that_way(tmp_path, client):
    """The point of taking the commits without the blobs: everything the agent
    reasons with — the log, a diff against the base — still works, and the push
    back still works. Against a local repo here, since what is under test is
    git's behaviour on the shape the CLI asks for, not the HTTP hop."""
    from tests.support import git_store

    pid = _project(client)
    origin: Path = git_store.ensure_repo(uuid.UUID(pid))

    def git(cwd: Path, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    git(origin, "config", "uploadpack.allowFilter", "true")

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
