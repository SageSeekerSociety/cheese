"""A member handing a task in commits its checkout under their own name.

The command the platform runs on the task's sandbox (`machine.hand_in
.commit_command`) is run here as a sandbox runs it: in a shell whose `cheese`
is the real CLI, against a bare repository standing in for the project's
forge. What it leaves is what the people reading the project see: one commit
on the task's branch with the member as its author and every saved file in it,
and a checkout with nothing left uncommitted.
"""

import os
import subprocess
import sys

import pytest

from app.domain.machine import hand_in
from tests.support.task_platform import CLI, TaskPlatform, git, load_cli

MEMBER = "Lin Xiao <linxiao@zhishi.local>"


@pytest.fixture
def platform(tmp_path):
    platform = TaskPlatform(tmp_path, forge_kind="forgejo")
    try:
        yield platform
    finally:
        platform.close()


@pytest.fixture
def checkout(tmp_path, monkeypatch, platform):
    """The task opened in a sandbox, its branch already on the forge."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    worktree = load_cli(platform)._task_worktree(platform.task_id)
    (worktree / ".gitignore").write_text("*.log\n")
    git(worktree, "add", ".gitignore")
    git(worktree, "commit", "-qm", "Ignore logs")
    load_cli(platform)._sync_all_tasks()
    return worktree


def run(tmp_path, platform, *, message: str) -> str:
    """The command, in a shell on the sandbox: `cheese` is the CLI."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    cheese = bin_dir / "cheese"
    cheese.write_text(f'#!/bin/sh\nexec {sys.executable} {CLI} "$@"\n')
    cheese.chmod(0o755)
    done = subprocess.run(
        [
            "bash",
            "-c",
            hand_in.commit_command(platform.task_id, author=MEMBER, message=message),
        ],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CHEESE_API": platform.url,
            "CHEESE_PROJECT": platform.project,
            "CHEESE_TOPIC": platform.room,
            "CHEESE_TOKEN": "session-token",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout + done.stderr


def test_saved_files_land_on_the_branch_as_the_members_commit(
    tmp_path, platform, checkout
):
    (checkout / "report.txt").write_text("edited by a person\n")
    (checkout / "draft.md").write_text("new, never added\n")
    (checkout / "build.log").write_text("ignored\n")

    said = run(tmp_path, platform, message="Tighten the report\n\nOne line changed.")

    head = platform.branch_head()
    assert f"cheese-hand-in:head {head}" in said
    landed = git(platform.forge, "log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", head)
    author, committer, *message = landed.splitlines()
    assert author == MEMBER
    assert committer != MEMBER
    assert message[0] == "Tighten the report"
    assert git(platform.forge, "show", f"{head}:report.txt") == "edited by a person"
    assert git(platform.forge, "show", f"{head}:draft.md") == "new, never added"
    assert "build.log" not in git(platform.forge, "ls-tree", "--name-only", head)
    assert git(checkout, "status", "--porcelain") == ""


def test_nothing_uncommitted_makes_no_commit(tmp_path, platform, checkout):
    head = platform.branch_head()

    said = run(tmp_path, platform, message="Nothing to say")

    assert "cheese-hand-in:committed" not in said
    assert platform.branch_head() == head
    assert f"cheese-hand-in:head {head}" in said
