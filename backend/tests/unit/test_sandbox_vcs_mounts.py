"""Sandbox container VCS mounts.

A topic's worktree is a linked git worktree (`_ensure_worktree`) that keeps
nothing but its files: HEAD, the index and every object live in the project's
shared repo, which the worktree finds through the relative `gitdir:` pointer in
its own `.git`. A sandbox container only ever gets the worktree, remapped to a
much shallower path (SANDBOX_WORKDIR) — without also mounting the main repo's
`.git` where that pointer resolves to, git walks off the container's root and
every git command in the sandbox fails with "not a git repository".

These tests reproduce that failure and its fix with plain directory copies —
no docker or mount namespace needed: copying a worktree to an unrelated path is
the same relocation a bind-mount-of-only-the-worktree performs.
"""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.workspace import service as ws


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def project(tmp_path, monkeypatch) -> uuid.UUID:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    return uuid.uuid4()


def test_mounts_land_where_the_real_pointer_resolves(project, tmp_path):
    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001 -- real worktree creation
    pointer = (wt / ".git").read_text().split(":", 1)[1].strip()

    mounts = ws.sandbox_vcs_mounts(project, topic, container_workdir="/work")

    assert mounts[0] == "-v"
    host, container = mounts[1].split(":", 1)

    # Independently derive where the pointer resolves once it is read from
    # /work instead of the real worktree path — must match what we mount.
    admin = Path(os.path.normpath(os.path.join("/work", pointer)))
    assert container == str(admin.parents[1])  # strip "worktrees/<name>"
    assert host == str(ws._repo(project) / ".git")  # noqa: SLF001


def test_worktree_alone_is_unusable_once_relocated(project, tmp_path):
    """Relocating (≈ bind-mounting) only the worktree breaks git, exactly like
    a sandbox container that gets no store mount would."""
    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    isolated = tmp_path / "container-sim" / "work"
    isolated.parent.mkdir(parents=True)
    shutil.copytree(wt, isolated)

    result = _git(isolated, "status")
    assert result.returncode != 0
    assert "not a git repository" in result.stderr.lower()


def test_the_mounts_make_the_relocated_worktree_a_working_repo(project, tmp_path):
    """The mounts sandbox_vcs_mounts() prescribes, applied via plain copies
    into a writable fake container root (standing in for real bind mounts at
    absolute container paths), give the isolated tree a git that can read its
    branch and commit onto it — which is the whole point: the agent's own
    commit is what moves the branch."""
    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    container_root = tmp_path / "container-sim"
    isolated = container_root / "work"
    isolated.parent.mkdir(parents=True)
    shutil.copytree(wt, isolated)

    mounts = ws.sandbox_vcs_mounts(project, topic, container_workdir=str(isolated))
    # mounts is ["-v", "host:container"]; the container side was computed
    # anchored at `isolated` itself (our fake /work), so it lands inside
    # container_root — apply it as a copy (a bind mount's observable effect,
    # minus needing real mount privileges).
    host, container = mounts[1].split(":", 1)
    Path(container).parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(host, container)

    status = _git(isolated, "status", "--porcelain", "--branch")
    assert status.returncode == 0, status.stderr
    assert ws.branch_for_tree(topic) in status.stdout

    (isolated / "hello.txt").write_text("hi\n", encoding="utf-8")
    assert _git(isolated, "add", "-A").returncode == 0
    committed = _git(
        isolated,
        "-c",
        "user.name=芝士",
        "-c",
        "user.email=cheese@zhishi.local",
        "commit",
        "-m",
        "feat: work from the sandbox",
    )
    assert committed.returncode == 0, committed.stderr

    # The commit landed on the topic's branch in the shared store the mount
    # points at — no export, no push.
    listed = _git(
        Path(container).parent, "ls-tree", "--name-only", ws.branch_for_tree(topic)
    )
    assert "hello.txt" in listed.stdout
