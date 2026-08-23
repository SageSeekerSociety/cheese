"""A machine's push must land on an open topic, and survive what 采纳 does next.

`cheese-sync` cannot fail loudly — a Stop hook that errors takes the turn down —
so anything that rejects or undoes its push looks exactly like success, which is
the original bug: the agent works and the branch never moves. These two tests
pin the end state (the machine's file is in the branch) rather than any single
mechanism, so they still catch it if the workspace layout changes underneath.
"""

import subprocess
import uuid

from app.api.routes.git_http import _configure_for_push
from app.domain.workspace import service as ws


def _git(cwd, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30
    )


def _machine_pushes(repo, tmp_path, branch: str, filename: str):
    """What a provisioned machine does: clone, commit on the topic branch, push."""
    work = tmp_path / "machine"
    _git(tmp_path, "clone", "-q", str(repo), str(work))
    _git(work, "config", "user.email", "cheese@zhishi.local")
    _git(work, "config", "user.name", "芝士")
    _git(work, "checkout", "-q", "-B", branch, f"origin/{branch}")
    (work / filename).write_text("what the agent wrote on its own machine\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "machine work")
    return _git(work, "push", "origin", branch)


def test_a_machine_can_push_to_a_topic_that_has_a_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws"))
    project, topic = uuid.uuid4(), uuid.uuid4()
    repo = ws.ensure_repo(project)
    branch = ws.branch_for_place(topic)
    ws._ensure_worktree(project, topic)  # the topic is open on the platform

    _configure_for_push(repo)
    pushed = _machine_pushes(repo, tmp_path, branch, "from_machine.txt")

    assert pushed.returncode == 0, f"push rejected: {pushed.stderr}"
    listed = _git(repo, "ls-tree", "--name-only", branch).stdout
    assert "from_machine.txt" in listed


def test_a_later_platform_snapshot_does_not_drag_the_branch_back(tmp_path, monkeypatch):
    """采纳 snapshots the worktree first, and that moves the topic bookmark with
    --allow-backwards — from a workspace that is behind the machine's push."""
    monkeypatch.setattr(ws.settings, "workspace_root", str(tmp_path / "ws"))
    project, topic = uuid.uuid4(), uuid.uuid4()
    repo = ws.ensure_repo(project)
    branch = ws.branch_for_place(topic)
    wt = ws._ensure_worktree(project, topic)
    _configure_for_push(repo)

    assert _machine_pushes(repo, tmp_path, branch, "from_machine.txt").returncode == 0
    pushed = _git(repo, "rev-parse", branch).stdout.strip()

    # A human edits a file on the platform, then hits 采纳.
    (wt / "from_platform.txt").write_text("edited on the platform\n")
    ws.snapshot_worktree(project, topic, "采纳前快照")

    listed = _git(repo, "ls-tree", "--name-only", branch).stdout
    assert "from_machine.txt" in listed, (
        f"snapshot discarded the machine's work (branch was {pushed})"
    )
