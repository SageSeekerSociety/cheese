"""Task checkouts preserve independent commits and unfinished work on devices."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.domain.agent import environment_runner
from app.domain.project.environment import EnvironmentConfig

CLI = Path(__file__).resolve().parents[2] / "sandbox/cheese"


def git(cwd, *args):
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def device(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    project, room = str(uuid.uuid4()), str(uuid.uuid4())
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    git(seed, "config", "user.name", "worker")
    git(seed, "config", "user.email", "worker@example.com")
    (seed / "same.txt").write_text("base\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "base")
    remote = tmp_path / "origin.git"
    git(tmp_path, "clone", "--bare", str(seed), str(remote))
    tasks = {}
    for _ in range(2):
        task = str(uuid.uuid4())
        branch = f"task/{uuid.UUID(task).hex[:8]}"
        git(remote, "branch", branch, "main")
        tasks[task] = {
            "task_id": task,
            "room_id": room,
            "branch": branch,
            "base": "main",
            "closed": False,
        }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            task = self.path.rsplit("/", 1)[-1]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": tasks[task]}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for key, value in {
        "HOME": str(home),
        "CHEESE_API": f"http://127.0.0.1:{server.server_port}",
        "CHEESE_PROJECT": project,
        "CHEESE_TOPIC": room,
        "CHEESE_GIT_REMOTE": str(remote),
        "CHEESE_TOKEN": "test-secret",
        "PATH": str(CLI.parent) + os.pathsep + os.environ["PATH"],
    }.items():
        monkeypatch.setenv(key, value)
    loader = SourceFileLoader("task_cli", str(CLI))
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(loader.name, loader)
    )
    loader.exec_module(module)
    try:
        yield module, tasks, remote, home
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_committing_in_one_task_pushes_only_its_branch(device):
    cli, tasks, remote, _ = device
    first, second = tasks
    a, b = cli._task_worktree(first), cli._task_worktree(second)
    before = git(remote, "rev-parse", tasks[second]["branch"])
    (a / "same.txt").write_text("first\n")
    git(a, "add", "same.txt")
    git(a, "commit", "-m", "fix: first task")
    assert git(remote, "show", tasks[first]["branch"] + ":same.txt") == "first"
    assert git(remote, "rev-parse", tasks[second]["branch"]) == before
    assert (b / "same.txt").read_text() == "base\n"


def test_sync_backs_up_uncommitted_work_without_changing_index_or_pr_head(device):
    cli, tasks, remote, _ = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    head = git(work, "rev-parse", "HEAD")
    (work / "same.txt").write_text("staged\n")
    git(work, "add", "same.txt")
    (work / "same.txt").write_text("unstaged\n")
    before = git(work, "diff", "--cached")
    cli._sync_task(task)
    assert git(work, "diff", "--cached") == before
    assert git(work, "rev-parse", "HEAD") == head
    assert git(remote, "rev-parse", tasks[task]["branch"]) == head
    refs = git(
        remote,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/cheese/snapshots/task/{task}",
    ).splitlines()
    assert len(refs) == 1
    assert git(remote, "show", refs[0] + ":same.txt") == "unstaged"


def test_reopening_a_task_preserves_unpushed_commits_and_dirty_files(device):
    cli, tasks, _, _ = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / "same.txt").write_text("local commit\n")
    git(work, "add", "same.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "fix: local")
    head = git(work, "rev-parse", "HEAD")
    (work / "draft.txt").write_text("unfinished")
    assert cli._task_worktree(task) == work
    assert git(work, "rev-parse", "HEAD") == head
    assert (work / "draft.txt").read_text() == "unfinished"


def test_failed_push_leaves_work_and_a_durable_failure_log(device):
    cli, tasks, remote, _ = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    hook = remote / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    (work / "draft.txt").write_text("keep me")
    with pytest.raises(RuntimeError):
        cli._sync_task(task)
    log = Path(git(work, "rev-parse", "--absolute-git-dir")) / "cheese-sync.log"
    assert "failed RuntimeError" in log.read_text()
    assert "test-secret" not in log.read_text()
    assert (work / "draft.txt").read_text() == "keep me"


def test_failed_push_is_spooled_to_the_room_hook(device, monkeypatch):
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    hook = home / "cheese-hook"
    hook.write_text('#!/bin/sh\ncat >> "$HOME/hook.json"\n')
    hook.chmod(0o755)
    monkeypatch.setenv("PATH", str(home) + os.pathsep + os.environ["PATH"])
    reject = remote / "hooks/pre-receive"
    reject.write_text("#!/bin/sh\nexit 1\n")
    reject.chmod(0o755)
    (work / "unfinished.txt").write_text("keep me")
    with pytest.raises(RuntimeError):
        cli._sync_task(task)
    report = json.loads((home / "hook.json").read_text())
    assert report["hook_event_name"] == "CheeseSync"
    assert report["status"] == "failed"
    assert report["task_id"] == task
    assert report["branch"] == tasks[task]["branch"]


def test_rejected_concurrent_push_preserves_both_histories(device, tmp_path):
    cli, tasks, remote, _ = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    other = tmp_path / "other"
    git(tmp_path, "clone", "--branch", tasks[task]["branch"], str(remote), str(other))
    git(other, "config", "user.name", "other")
    git(other, "config", "user.email", "other@example.com")
    (other / "other.txt").write_text("other writer")
    git(other, "add", ".")
    git(other, "commit", "-m", "feat: another writer")
    git(other, "push", "origin", "HEAD")
    remote_head = git(other, "rev-parse", "HEAD")
    (work / "local.txt").write_text("local writer")
    git(work, "add", ".")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "feat: local writer")
    local_head = git(work, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError):
        cli._sync_task(task)
    assert git(remote, "rev-parse", tasks[task]["branch"]) == remote_head
    assert git(work, "rev-parse", "HEAD") == local_head
    assert (
        git(remote, "show", f"refs/cheese/snapshots/task/{task}/{local_head}:local.txt")
        == "local writer"
    )


def test_closed_task_sync_preserves_files_without_moving_its_delivered_branch(device):
    cli, tasks, remote, _ = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    head = git(remote, "rev-parse", tasks[task]["branch"])
    tasks[task]["closed"] = True
    (work / "draft.txt").write_text("late work")
    cli._sync_task(task)
    assert git(remote, "rev-parse", tasks[task]["branch"]) == head
    with pytest.raises(SystemExit):
        cli._task_worktree(task)


def test_renaming_a_checkout_cannot_deliver_it_as_another_task(device):
    cli, tasks, remote, _ = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    head = git(remote, "rev-parse", tasks[task]["branch"])
    git(work, "branch", "-m", "another-task")
    (work / "unfinished.txt").write_text("retained")
    with pytest.raises(RuntimeError, match="Expected task branch"):
        cli._sync_task(task)
    assert git(remote, "rev-parse", tasks[task]["branch"]) == head
    assert not git(remote, "branch", "--list", "another-task")
    assert (work / "unfinished.txt").read_text() == "retained"


# Which directory holds the runner is the launcher's choice, not the CLI's: the
# machine launcher writes `.cheese/`, the claude-code remote-execution payload
# writes its own config dir. Opening a task named one of them, so a room on a
# machine prepared by the other launcher died before it could print the task id
# — the checkout and the task record were already on disk, and the room saw
# nothing happen at all.
@pytest.mark.parametrize("shipped_by", (".cheese", ".claude"))
def test_room_starts_before_task_dependencies_and_worktree_prepares_each_branch(
    device, shipped_by
):
    _, tasks, remote, home = device
    seed = remote.parent / "seed"
    for directory in ("backend", "frontend"):
        (seed / directory).mkdir()
        (seed / directory / "dependency-version").write_text("1")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Add dependency fixtures")
    git(seed, "push", str(remote), "main")
    for data in tasks.values():
        git(remote, "branch", "-f", data["branch"], "main")
    room = home / "room"
    room.mkdir()
    helpers = home / shipped_by
    helpers.mkdir()
    shutil.copy(environment_runner.__file__, helpers / "cheese-environment.py")
    config = EnvironmentConfig(
        setup_script=(
            'echo setup >> "$HOME/setup-count"\n'
            'mkdir -p "$HOME/.local/bin"\n'
            "printf '#!/bin/sh\\ncat dependency-version\\n' "
            '> "$HOME/.local/bin/install-dependencies"\n'
            'chmod +x "$HOME/.local/bin/install-dependencies"\n'
            "export ONLY_SETUP=yes"
        ),
        startup_script=(
            "test ! -f fail-install || exit 23\n"
            "(cd backend && install-dependencies > installed-version)\n"
            "(cd frontend && install-dependencies > installed-version)\n"
            'printf "%s" "$PROJECT_VALUE" > project-value\n'
            'test -z "${ONLY_SETUP:-}"\n'
            "echo startup >> startup-count"
        ),
        variables={"PROJECT_VALUE": "$(touch injected) 'literal'"},
    )
    started = subprocess.run(
        [sys.executable, environment_runner.__file__, "touch", "agent-started"],
        env={
            **os.environ,
            "CHEESE_WORK": str(room),
            "CHEESE_ENVIRONMENT": json.dumps(config.snapshot()),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert started.returncode == 0, started.stderr
    assert (room / "agent-started").exists()
    assert not (room / "backend").exists()

    def open_task(task):
        return subprocess.run(
            [sys.executable, str(CLI), "worktree", task],
            cwd=room,
            capture_output=True,
            text=True,
            timeout=15,
        )

    first, second = tasks
    opened = open_task(first)
    assert opened.returncode == 0, opened.stderr
    first_work = Path(opened.stdout.strip())
    opened = open_task(second)
    assert opened.returncode == 0, opened.stderr
    second_work = Path(opened.stdout.strip())
    assert first_work != second_work
    for work in (first_work, second_work):
        for directory in ("backend", "frontend"):
            assert (work / directory / "installed-version").read_text() == "1"
        assert (work / "project-value").read_text() == config.variables["PROJECT_VALUE"]
        assert not (work / "injected").exists()
    (first_work / "backend/dependency-version").write_text("2")
    (first_work / "draft.txt").write_text("unfinished work")
    assert open_task(first).returncode == 0
    assert (first_work / "backend/installed-version").read_text() == "2"
    assert (second_work / "backend/installed-version").read_text() == "1"
    assert (first_work / "startup-count").read_text() == "startup\nstartup\n"

    (first_work / "fail-install").touch()
    failed = open_task(first)
    assert failed.returncode == 23
    assert not failed.stdout.strip(), "A failed task must not be returned as ready"
    assert "startup script exited with status 23" in failed.stderr
    assert (first_work / "draft.txt").read_text() == "unfinished work"
    root = home / ".cheese-environment"
    assert json.loads((root / "status.json").read_text())["state"] == "ready"
    task_status = json.loads((root / "tasks" / first / "status.json").read_text())
    assert task_status["state"] == "failed"
    assert task_status["stage"] == "startup"
    assert task_status["exit_code"] == 23
    (first_work / "fail-install").unlink()
    assert open_task(first).returncode == 0
    task_status = environment_runner.read_status(root / "tasks" / first)
    assert task_status["state"] == "complete"
    assert (home / "setup-count").read_text() == "setup\n"
    assert (first_work / "draft.txt").read_text() == "unfinished work"
