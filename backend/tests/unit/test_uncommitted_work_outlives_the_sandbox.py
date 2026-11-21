"""What a task's checkout has not committed outlives the sandbox it is in.

The checkout is a real `cheese` CLI working against a bare repository standing
in for the project's Forgejo. A checkpoint (`cheese sync --all`) keeps the
uncommitted files; a new sandbox — another home, a fresh checkout of the same
task — finds them back as uncommitted changes, and the task's branch carries no
commit it did not make.
"""

import subprocess
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote

import pytest

from app.core.errors import UpstreamUnavailableError
from app.domain.machine import session_work
from app.domain.repository import forge_files, kept_work
from tests.support.task_platform import TaskPlatform, git, load_cli


@pytest.fixture
def platform(tmp_path):
    platform = TaskPlatform(tmp_path, forge_kind="forgejo")
    try:
        yield platform
    finally:
        platform.close()


def sandbox(tmp_path, monkeypatch, platform, name: str):
    """A new sandbox for the task: its own home, and the task opened in it."""
    home = tmp_path / name
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return load_cli(platform)._task_worktree(platform.task_id)


def status(worktree) -> list[str]:
    said = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted(said.splitlines())


def kept_refs(platform) -> str:
    return git(platform.forge, "for-each-ref", "refs/cheese/")


def started(tmp_path, monkeypatch, platform):
    """A checkout whose branch is on the forge with a `.gitignore` of its own."""
    worktree = sandbox(tmp_path, monkeypatch, platform, "first")
    (worktree / ".gitignore").write_text("*.log\n")
    git(worktree, "add", ".gitignore")
    git(worktree, "commit", "-qm", "Ignore logs")
    load_cli(platform)._sync_all_tasks()
    return worktree


def move_branch(tmp_path, platform, branch: str | None = None) -> None:
    """Someone else pushes a commit to the task's branch (or to ``branch``)."""
    branch = branch or platform.branch
    elsewhere = tmp_path / f"elsewhere-{branch.replace('/', '-')}"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "-b",
            branch,
            str(platform.forge),
            str(elsewhere),
        ],
        check=True,
    )
    git(elsewhere, "config", "user.name", "Someone")
    git(elsewhere, "config", "user.email", "someone@users.invalid")
    (elsewhere / "notes.txt").write_text("moved on\n")
    git(elsewhere, "commit", "-qam", "Moved on")
    git(elsewhere, "push", "-q", "origin", branch)


def test_uncommitted_changes_come_back_in_a_new_sandbox_without_a_commit(
    tmp_path, monkeypatch, platform
):
    worktree = started(tmp_path, monkeypatch, platform)
    head = platform.branch_head()
    (worktree / "report.txt").write_text("edited, not committed\n")
    (worktree / "notes.txt").unlink()
    (worktree / "draft.md").write_text("new, never added\n")
    (worktree / "build.log").write_text("ignored\n")
    before = status(worktree)

    load_cli(platform)._sync_all_tasks()

    assert platform.branch_head() == head
    again = sandbox(tmp_path, monkeypatch, platform, "second")
    assert status(again) == [" D notes.txt", " M report.txt", "?? draft.md"]
    assert before == [" D notes.txt", " M report.txt", "?? draft.md"]
    assert (again / "report.txt").read_text() == "edited, not committed\n"
    assert not (again / "build.log").exists()
    assert git(again, "diff", "--cached", "--name-only") == ""
    assert git(again, "rev-parse", "HEAD") == head
    assert platform.branch_head() == head


def test_a_clean_checkout_leaves_nothing_kept(tmp_path, monkeypatch, platform):
    worktree = started(tmp_path, monkeypatch, platform)
    (worktree / "draft.md").write_text("for a moment\n")
    load_cli(platform)._sync_all_tasks()
    assert kept_refs(platform)

    (worktree / "draft.md").unlink()
    load_cli(platform)._sync_all_tasks()

    assert kept_refs(platform) == ""
    assert status(sandbox(tmp_path, monkeypatch, platform, "second")) == []


def test_changes_are_not_laid_over_a_branch_that_moved_since(
    tmp_path, monkeypatch, platform, capsys
):
    worktree = started(tmp_path, monkeypatch, platform)
    (worktree / "report.txt").write_text("kept on the old head\n")
    load_cli(platform)._sync_all_tasks()
    move_branch(tmp_path, platform)
    capsys.readouterr()

    again = sandbox(tmp_path, monkeypatch, platform, "second")

    assert status(again) == []
    assert (again / "notes.txt").read_text() == "moved on\n"
    assert "没提交的改动没有放回" in capsys.readouterr().err
    # Still on the forge, for whoever redoes them.
    assert kept_refs(platform)


@pytest.fixture
def forge_reads(monkeypatch, platform):
    """The platform's reads of the forge, answered from the bare repository
    the CLI pushed to, the way Forgejo's API answers them."""

    async def binding(project_id, session):
        return SimpleNamespace(kind=platform.forge_kind)

    async def repository_data(project_id, session, path="", **_):
        if path.startswith("/compare/"):
            base, _, sha = unquote(path.removeprefix("/compare/")).partition("...")
            behind = subprocess.run(
                ["git", "-C", str(platform.forge), "merge-base", "--is-ancestor"]
                + [sha, f"refs/heads/{base}"],
            ).returncode
            return {"total_commits": behind}
        ref = unquote(path.removeprefix("/git/commits/"))
        try:
            sha = git(platform.forge, "rev-parse", "--verify", "-q", ref)
        except subprocess.CalledProcessError:
            return None
        parents, date = git(platform.forge, "log", "-1", "--format=%P%n%cI", sha).split(
            "\n"
        )
        return {
            "sha": sha,
            "parents": [{"sha": parent} for parent in parents.split()],
            "commit": {"committer": {"date": date}},
        }

    async def branch_head(project_id, session, branch, **_):
        return platform.branch_head()

    monkeypatch.setattr(kept_work, "binding_for_project", binding)
    monkeypatch.setattr(kept_work, "repository_data", repository_data)
    monkeypatch.setattr(kept_work, "branch_head", branch_head)


async def what_a_person_reads(monkeypatch, platform) -> str:
    """What a person saving the task's files is told while its sandbox is
    gone and no new one has taken the task up yet (opening them reads the
    forge, `test_task_files_on_its_own_machine`)."""
    task = SimpleNamespace(
        id=uuid.UUID(platform.task_id),
        room_id=uuid.uuid4(),
        branch_name=platform.branch,
        base_branch="main",
    )
    room = SimpleNamespace(id=task.room_id, resource_id=None)
    files = forge_files.ProjectFiles(
        SimpleNamespace(get=AsyncMock(return_value=room)),
        uuid.UUID(platform.project),
        task.id,
        executor=AsyncMock(),
    )
    monkeypatch.setattr(files, "task", AsyncMock(return_value=task))
    monkeypatch.setattr(
        forge_files.environment_rows,
        "working_copy",
        AsyncMock(return_value=({"kind": "device"}, True)),
    )
    with pytest.raises(UpstreamUnavailableError) as raised:
        await files.live("write", path="report.txt", content="x\n", version="0")
    return str(raised.value)


async def what_the_teammate_reads(platform) -> str:
    """What the task's teammate is told on its first call in a new sandbox."""
    row = SimpleNamespace(first=lambda: (platform.branch, "main"))
    db = SimpleNamespace(execute=AsyncMock(return_value=row))
    return await session_work._lost_notice(
        db, uuid.UUID(platform.project), uuid.UUID(platform.task_id)
    )


@pytest.mark.anyio
async def test_people_and_the_teammate_hear_the_changes_come_back(
    tmp_path, monkeypatch, platform, forge_reads
):
    worktree = started(tmp_path, monkeypatch, platform)
    (worktree / "report.txt").write_text("edited, not committed\n")
    load_cli(platform)._sync_all_tasks()

    assert "会放回" in await what_a_person_reads(monkeypatch, platform)
    told = await what_the_teammate_reads(platform)
    assert "原样放回" in told
    assert "不会放回" not in told


@pytest.mark.anyio
async def test_people_and_the_teammate_hear_when_the_branch_moved(
    tmp_path, monkeypatch, platform, forge_reads
):
    worktree = started(tmp_path, monkeypatch, platform)
    (worktree / "report.txt").write_text("kept on the old head\n")
    load_cli(platform)._sync_all_tasks()
    move_branch(tmp_path, platform)
    kept = git(platform.forge, "rev-parse", f"refs/cheese/kept/{platform.task_id}")

    assert "不会自动放回" in await what_a_person_reads(monkeypatch, platform)
    told = await what_the_teammate_reads(platform)
    assert "不会放回" in told
    assert kept[:12] in told


@pytest.mark.anyio
async def test_a_task_with_no_commit_of_its_own_gets_its_changes_back_after_the_base_moved(
    tmp_path, monkeypatch, platform, forge_reads
):
    worktree = sandbox(tmp_path, monkeypatch, platform, "first")
    (worktree / "report.txt").write_text("first edit, nothing pushed\n")
    load_cli(platform)._sync_all_tasks()
    move_branch(tmp_path, platform, "main")

    assert "会放回" in await what_a_person_reads(monkeypatch, platform)
    assert "不会放回" not in await what_the_teammate_reads(platform)
    again = sandbox(tmp_path, monkeypatch, platform, "second")
    assert status(again) == [" M report.txt"]
    assert (again / "report.txt").read_text() == "first edit, nothing pushed\n"
