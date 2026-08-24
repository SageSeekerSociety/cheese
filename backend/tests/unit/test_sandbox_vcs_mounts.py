"""Sandbox container VCS mounts.

A topic's worktree is a jj workspace (`_ensure_worktree`) whose `.jj/repo`
pointer is a path *relative to the real host directory nesting* between the
worktree and the project's shared main repo store — see
`ws.sandbox_vcs_mounts`. A sandbox container only ever gets the worktree,
remapped to a much shallower path (SANDBOX_WORKDIR) — without also mounting
the main repo's `.jj`/`.git` where that unmodified pointer resolves to, `jj`
walks off the container's root and every jj/git command in the sandbox fails
with "Cannot access ../../../../<project_id>/.jj/repo".

These tests reproduce that failure and its fix with plain directory copies —
no docker or mount namespace needed: copying a jj workspace to an unrelated
path is the same relocation a bind-mount-of-only-the-worktree performs.
"""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.workspace import service as ws


def _jj(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["jj", "--no-pager", *args], cwd=repo, capture_output=True, text=True
    )


@pytest.fixture
def project(tmp_path, monkeypatch) -> uuid.UUID:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    return uuid.uuid4()


def test_mounts_land_where_the_real_jj_pointer_resolves(project, tmp_path):
    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001 -- exercising real jj workspace creation
    pointer = (wt / ".jj" / "repo").read_text()

    mounts = ws.sandbox_vcs_mounts(project, topic, container_workdir="/work")

    assert mounts[0] == "-v"
    jj_host, jj_container = mounts[1].split(":", 1)
    assert mounts[2] == "-v"
    git_host, git_container = mounts[3].split(":", 1)

    # Independently derive where the pointer resolves once it's read from
    # /work instead of the real worktree path — must match what we mount.
    expected_store = Path(os.path.normpath(os.path.join("/work", ".jj", pointer)))
    expected_main_in_container = expected_store.parents[1]  # strip ".jj/repo"
    assert jj_container == str(expected_main_in_container / ".jj")
    assert git_container == str(expected_main_in_container / ".git")

    assert jj_host == str(ws._repo(project) / ".jj")  # noqa: SLF001
    assert git_host == str(ws._repo(project) / ".git")  # noqa: SLF001


def test_worktree_alone_is_unusable_once_relocated(project, tmp_path):
    """Reproduces the reported bug: relocating (≈ bind-mounting) only the
    worktree breaks jj, exactly like the sandbox container does today."""
    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    isolated = tmp_path / "container-sim" / "work"
    isolated.parent.mkdir(parents=True)
    shutil.copytree(wt, isolated)

    result = _jj(isolated, "status")
    assert result.returncode != 0
    assert "Cannot access" in result.stderr


def test_fix_makes_jj_work_inside_the_isolated_worktree(project, tmp_path):
    """The mounts sandbox_vcs_mounts() prescribes, applied via plain copies
    into a writable fake container root (standing in for real bind mounts at
    absolute container paths), restore jj/git/log/diff in the isolated tree."""
    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    container_root = tmp_path / "container-sim"
    isolated = container_root / "work"
    isolated.parent.mkdir(parents=True)
    shutil.copytree(wt, isolated)

    mounts = ws.sandbox_vcs_mounts(project, topic, container_workdir=str(isolated))
    # mounts is ["-v", "host:container", "-v", "host:container"]; container
    # sides were computed anchored at `isolated` itself (our fake /work), so
    # they land inside container_root — apply them as copies (a bind mount's
    # observable effect, minus needing real mount privileges).
    for i in (1, 3):
        host, container = mounts[i].split(":", 1)
        dst = Path(container)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(host, dst)

    status = _jj(isolated, "status")
    assert status.returncode == 0, status.stderr
    assert "no changes" in status.stdout.lower() or "Working copy" in status.stdout

    (isolated / "hello.txt").write_text("hi\n", encoding="utf-8")
    diff = _jj(isolated, "diff")
    assert diff.returncode == 0, diff.stderr
    assert "hello.txt" in diff.stdout

    log = _jj(isolated, "log", "--no-graph", "-T", "description")
    assert log.returncode == 0, log.stderr
