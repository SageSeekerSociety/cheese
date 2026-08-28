"""A tree's directory already exists — keep what is in it.

Every workspace directory on disk was made by something other than
`git worktree add`, and `_tree_dirname` exists precisely because those names
cannot move: the container workdir, the mount depth, and the tmux session name
a device hashes out of that workdir all read them. So a directory that is
already there has to gain a git checkout WHERE IT SITS.

What makes that worth a test rather than a comment: the files in it are the only
copy of whatever the agent had not committed. Deleting and re-checking-out would
produce a directory that looks right — same name, same tracked content, clean
`git status` — with that work silently gone.
"""

import subprocess
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.domain.workspace import service as ws


@pytest.fixture
def project(tmp_path, monkeypatch) -> uuid.UUID:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    return uuid.uuid4()


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout


def _a_tree_someone_else_made(project: uuid.UUID, topic: uuid.UUID) -> Path:
    """The shape every existing workspace has: the topic's directory, holding
    files, with another VCS's metadata beside them and no `.git` of its own."""
    wt = ws._worktree_path(project, topic)  # noqa: SLF001
    (wt / ".jj" / "working_copy").mkdir(parents=True)
    (wt / ".jj" / "repo").write_text("../../../../elsewhere/.jj/repo")
    (wt / "src").mkdir()
    (wt / "src" / "app.py").write_text(
        "print('half a day of work')\n", encoding="utf-8"
    )
    return wt


def test_the_files_survive_becoming_a_worktree(project):
    topic = uuid.uuid4()
    existing = _a_tree_someone_else_made(project, topic)

    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    assert wt == existing  # same directory, not a rebuilt one next door
    assert (wt / "src" / "app.py").read_text(encoding="utf-8") == (
        "print('half a day of work')\n"
    )


def test_what_was_uncommitted_still_reads_as_uncommitted(project):
    """Not merely preserved on disk — preserved AS work git can see, so the next
    `git add` carries it onto the branch like any other change."""
    topic = uuid.uuid4()
    ws._ensure_worktree(project, topic)  # noqa: SLF001 -- an ordinary tree first
    wt = ws._worktree_path(project, topic)  # noqa: SLF001
    _git(wt, "config", "user.email", "cheese@zhishi.local")
    _git(wt, "config", "user.name", "芝士")
    (wt / "committed.md").write_text("delivered\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "feat: delivered")

    # Now put it back into the shape a pre-worktree directory has, keeping an
    # edit the agent never committed.
    (wt / ".git").unlink()
    (wt / ".jj").mkdir()
    (wt / "committed.md").write_text("delivered, then edited\n", encoding="utf-8")
    _git(ws._repo(project), "worktree", "prune")  # noqa: SLF001

    readopted = ws._ensure_worktree(project, topic)  # noqa: SLF001

    assert "committed.md" in _git(readopted, "status", "--porcelain")
    assert _git(readopted, "rev-parse", "--abbrev-ref", "HEAD").strip() == (
        ws.branch_for_tree(topic)
    )
    # The commit that WAS delivered is still on the branch behind it.
    assert "feat: delivered" in _git(readopted, "log", "-1", "--format=%s")


def test_the_other_vcs_metadata_does_not_survive(project):
    """Left in place it is a trap: a `.jj` beside a `.git` reads as a live
    colocated repo to anyone who opens the directory, and to any tool that
    looks for one."""
    topic = uuid.uuid4()
    wt = _a_tree_someone_else_made(project, topic)

    ws._ensure_worktree(project, topic)  # noqa: SLF001

    assert not (wt / ".jj").exists()
    assert (wt / ".git").is_file()  # a linked worktree's pointer, not a repo


def test_an_empty_directory_is_just_a_worktree(project):
    """The ordinary path is not affected by any of the above."""
    topic = uuid.uuid4()
    ws._worktree_path(project, topic).mkdir(parents=True)  # noqa: SLF001

    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    assert _git(wt, "status", "--porcelain") == ""
