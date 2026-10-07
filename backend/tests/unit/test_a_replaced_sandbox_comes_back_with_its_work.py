"""A sandbox is disposable: the one that replaces it gets the task's work back.

What survives a sandbox is the task branch on the forge and the platform's
snapshot of what was not pushed, both written by each turn's checkpoint
(`cheese sync --all`). A fresh sandbox opening the task (`cheese worktree`)
brings the snapshot back into the checkout and tells the agent what came back;
whatever it cannot bring back without overwriting something is kept beside it.
"""

import contextlib
import io
import subprocess

import pytest

from tests.support.task_platform import TaskPlatform, git, load_cli


@pytest.fixture
def platform(tmp_path):
    platform = TaskPlatform(tmp_path)
    yield platform
    platform.close()


def on_machine(monkeypatch, tmp_path, name):
    """A fresh sandbox: a home of its own, with nothing of the last one."""
    home = tmp_path / name
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def open_task(cli, task_id) -> tuple:
    """`cheese worktree <task>` as the agent runs it: the path, and what it was told."""
    told = io.StringIO()
    with contextlib.redirect_stderr(told):
        worktree = cli._task_worktree(task_id)
    return worktree, told.getvalue()


def status(worktree) -> list[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


def test_a_replaced_sandbox_gets_its_uncommitted_and_untracked_work_back(
    monkeypatch, tmp_path, platform
):
    cli = load_cli(platform)
    on_machine(monkeypatch, tmp_path, "first-sandbox")
    first, _ = open_task(cli, platform.task_id)
    (first / "report.txt").write_text("half-written edit\n")
    (first / "draft.md").write_text("a file nobody committed\n")
    cli._sync_all_tasks()  # The turn's checkpoint.
    pushed = platform.branch_head()

    on_machine(monkeypatch, tmp_path, "second-sandbox")
    second, told = open_task(cli, platform.task_id)

    assert (second / "report.txt").read_text() == "half-written edit\n"
    assert (second / "draft.md").read_text() == "a file nobody committed\n"
    assert sorted(status(second)) == [" M report.txt", "?? draft.md"]
    assert git(second, "rev-parse", "HEAD") == pushed
    assert platform.task_id in told
    assert "2 个文件" in told
    assert platform.snapshots[-1]["created_at"][:10] in told
    for not_carried in ("依赖", "/tmp", "进程"):
        assert not_carried in told

    # Opening it again is free: nothing is restored twice, nothing is said.
    again, told_again = open_task(cli, platform.task_id)
    assert again == second
    assert told_again == ""
    assert (second / "report.txt").read_text() == "half-written edit\n"


def test_a_commit_whose_push_failed_comes_back_on_the_branch(
    monkeypatch, tmp_path, platform
):
    cli = load_cli(platform)
    on_machine(monkeypatch, tmp_path, "first-sandbox")
    first, _ = open_task(cli, platform.task_id)
    (first / "report.txt").write_text("committed\n")
    git(first, "commit", "-qam", "Report")
    committed = git(first, "rev-parse", "HEAD")
    (first / "notes.txt").write_text("not yet\n")
    away = platform.forge.with_name("forge-unreachable")
    platform.forge.rename(away)
    with pytest.raises(SystemExit):
        cli._sync_all_tasks()  # Backed up; the push did not go through.
    away.rename(platform.forge)
    assert platform.branch_head() != committed

    on_machine(monkeypatch, tmp_path, "second-sandbox")
    second, told = open_task(cli, platform.task_id)

    assert git(second, "rev-parse", "HEAD") == committed
    assert (second / "notes.txt").read_text() == "not yet\n"
    assert status(second) == [" M notes.txt"]
    assert "1 个没推送的提交" in told
    cli._sync_task(platform.task_id)
    assert platform.branch_head() == committed


def test_a_snapshot_behind_the_branch_is_kept_aside_beside_the_new_work(
    monkeypatch, tmp_path, platform
):
    cli = load_cli(platform)
    on_machine(monkeypatch, tmp_path, "first-sandbox")
    first, _ = open_task(cli, platform.task_id)
    (first / "report.txt").write_text("the snapshot's edit\n")
    cli._sync_all_tasks()
    # Someone else moves the branch on after that checkpoint.
    elsewhere = tmp_path / "elsewhere"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "-b",
            platform.branch,
            str(platform.forge),
            str(elsewhere),
        ],
        check=True,
    )
    git(elsewhere, "config", "user.name", "Someone")
    git(elsewhere, "config", "user.email", "someone@users.invalid")
    (elsewhere / "report.txt").write_text("pushed from elsewhere\n")
    git(elsewhere, "commit", "-qam", "Elsewhere")
    git(elsewhere, "push", "-q", "origin", platform.branch)

    home = on_machine(monkeypatch, tmp_path, "second-sandbox")
    second, told = open_task(cli, platform.task_id)

    assert (second / "report.txt").read_text() == "pushed from elsewhere\n"
    assert status(second) == []
    kept = list((home / ".cheese" / "recovered").iterdir())
    assert len(kept) == 1
    assert (kept[0] / "report.txt").read_text() == "the snapshot's edit\n"
    assert str(kept[0]) in told


def test_work_in_a_fresh_checkout_is_never_overwritten_and_never_replaces_the_snapshot(
    monkeypatch, tmp_path, platform
):
    cli = load_cli(platform)
    on_machine(monkeypatch, tmp_path, "first-sandbox")
    first, _ = open_task(cli, platform.task_id)
    (first / "report.txt").write_text("the snapshot's edit\n")
    cli._sync_all_tasks()
    snapshot = platform.snapshots[-1]["snapshot_sha"]

    home = on_machine(monkeypatch, tmp_path, "second-sandbox")
    platform.downloads_failing = 1
    second, told = open_task(cli, platform.task_id)
    assert "没能从平台快照恢复" in told
    assert (second / "report.txt").read_text() == "base\n"

    # A checkpoint of the checkout that missed its restore backs nothing up.
    cli._sync_all_tasks()
    assert platform.snapshots[-1]["snapshot_sha"] == snapshot

    # The agent works on it anyway; the next checkpoint keeps both.
    (second / "notes.txt").write_text("new work in the fresh sandbox\n")
    with contextlib.redirect_stderr(io.StringIO()):
        cli._sync_all_tasks()
    assert (second / "notes.txt").read_text() == "new work in the fresh sandbox\n"
    assert (second / "report.txt").read_text() == "base\n"
    kept = next((home / ".cheese" / "recovered").iterdir())
    assert (kept / "report.txt").read_text() == "the snapshot's edit\n"
    # And from then on, the fresh checkout is what is backed up.
    latest = platform.snapshots[-1]["snapshot_sha"]
    assert latest != snapshot
    assert git(second, "show", f"{latest}:notes.txt") == "new work in the fresh sandbox"
