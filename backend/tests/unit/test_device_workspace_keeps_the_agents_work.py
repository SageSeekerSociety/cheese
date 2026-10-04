"""Task checkouts preserve independent commits and unfinished work on devices."""

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import urllib.error
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
            "remote": str(remote),
            "coauthors": [],
        }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path.startswith("/topics/"):
                # What the room is told, kept where a test can read it.
                with (home / "posted.jsonl").open("a") as posted:
                    posted.write(
                        json.dumps(
                            {
                                "path": self.path,
                                "token": self.headers["X-Cheese-Token"],
                                "body": json.loads(payload),
                            }
                        )
                        + "\n"
                    )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data": {}}')
                return
            self.do_GET()

        def do_PUT(self):
            resets = home / "resets"
            if resets.exists() and int(resets.read_text()) > 0:
                # What a flaky link or an edge proxy does to an upload it drops.
                resets.write_text(str(int(resets.read_text()) - 1))
                self.connection.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )
                self.close_connection = True
                self.connection.close()
                return
            task, _, snapshot = self.path.rsplit("/", 3)[1:]
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            limit = home / "upload-limit"
            if limit.exists() and len(payload) > int(limit.read_text()):
                # The body limit of whatever sits between machine and platform.
                self.send_response(413)
                self.end_headers()
                return
            digest = hashlib.sha256(payload).hexdigest()
            assert digest == self.headers["X-Content-SHA256"]
            backups = home / "backups" / task
            backups.mkdir(parents=True, exist_ok=True)
            (backups / f"{snapshot}.bundle").write_bytes(payload)
            # The platform's latest backup of the task is the last one it took.
            (backups / "latest.json").write_text(
                json.dumps(
                    {
                        "id": snapshot,
                        "snapshot_sha": snapshot,
                        "head_sha": self.headers["X-Cheese-Head"],
                        "digest": digest,
                    }
                )
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": {"digest": digest}}).encode())

        def do_GET(self):
            if "/snapshots/" in self.path:
                # What `cheese recover` reads: a task's backup, described or whole.
                task, _, which = self.path.rsplit("/", 3)[1:]
                backups = home / "backups" / task
                if which == "latest":
                    if not (backups / "latest.json").exists():
                        self.send_response(404)
                        self.end_headers()
                        return
                    latest = json.loads((backups / "latest.json").read_text())
                    body = json.dumps({"data": latest}).encode()
                else:
                    body = (backups / f"{which}.bundle").read_bytes()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
                return
            task = self.path.rsplit("/", 1)[-1]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"data": tasks[task]}).encode())

        def log_request(self, *_):
            # Every call the machine made to the platform, for a test to count.
            with (home / "requests.log").open("a") as seen:
                seen.write(f"{self.command} {self.path}\n")

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


def test_another_worktree_beside_a_task_commits_and_leaves_the_task_alone(
    device, tmp_path
):
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    branch = tasks[task]["branch"]
    delivered = git(remote, "rev-parse", branch)
    extra = tmp_path / "experiment"
    git(work, "worktree", "add", "-b", "experiment", str(extra), "main")
    (extra / "same.txt").write_text("experiment\n")
    git(extra, "add", "same.txt")

    committed = subprocess.run(
        ["git", "-C", str(extra), "commit", "-m", "test: experiment"],
        capture_output=True,
        text=True,
    )

    assert committed.returncode == 0, committed.stderr
    assert "[cheese]" not in committed.stderr
    assert git(extra, "show", "HEAD:same.txt") == "experiment"
    assert git(remote, "rev-parse", branch) == delivered
    assert git(work, "rev-parse", "HEAD") == delivered
    assert (work / "same.txt").read_text() == "base\n"
    assert not git(remote, "branch", "--list", "experiment")


def test_sync_backs_up_uncommitted_work_without_changing_index_or_pr_head(device):
    cli, tasks, remote, home = device
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
    assert refs == []
    (bundle,) = (home / "backups" / task).glob("*.bundle")
    recovered = home / "recovered"
    git(home, "clone", str(remote), str(recovered))
    git(recovered, "fetch", str(bundle), f"refs/cheese/snapshots/{task}")
    assert git(recovered, "show", "FETCH_HEAD:same.txt") == "unstaged"


def test_sync_all_touches_only_the_tasks_with_something_to_sync(device):
    """A room keeps every task it ever opened, so the push before a switch (and
    at every Stop) has to cost what is unpushed, not how old the room is."""
    cli, tasks, remote, home = device
    template = next(iter(tasks.values()))
    for _ in range(6):
        task = str(uuid.uuid4())
        branch = f"task/{uuid.UUID(task).hex[:8]}"
        git(remote, "branch", branch, "main")
        tasks[task] = {**template, "task_id": task, "branch": branch}
    worktrees = {}
    for task in tasks:
        work = worktrees[task] = cli._task_worktree(task)
        (work / "done.txt").write_text(f"finished {task}\n")
        git(work, "add", "done.txt")
        git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "finished work")
        cli._sync_task(task)
    committed, edited, *finished = tasks
    work = worktrees[committed]
    (work / "more.txt").write_text("not pushed yet\n")
    git(work, "add", "more.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "more work")
    (worktrees[edited] / "draft.txt").write_text("not committed\n")
    (home / "requests.log").unlink()
    shutil.rmtree(home / "backups")

    cli._sync_all_tasks()

    assert git(remote, "rev-parse", tasks[committed]["branch"]) == git(
        work, "rev-parse", "HEAD"
    )
    assert {path.name for path in (home / "backups").iterdir()} == {
        committed,
        edited,
    }
    seen = (home / "requests.log").read_text()
    assert committed in seen and edited in seen
    assert not [task for task in finished if task in seen]


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
    git(work, "add", "draft.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "local work")
    with pytest.raises(RuntimeError):
        cli._sync_task(task)
    log = Path(git(work, "rev-parse", "--absolute-git-dir")) / "cheese-sync.log"
    assert "failed RuntimeError" in log.read_text()
    assert "test-secret" not in log.read_text()
    assert (work / "draft.txt").read_text() == "keep me"


def test_failed_push_is_told_to_the_room(device):
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    reject = remote / "hooks/pre-receive"
    reject.write_text("#!/bin/sh\nexit 1\n")
    reject.chmod(0o755)
    (work / "unfinished.txt").write_text("keep me")
    git(work, "add", "unfinished.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "local work")
    with pytest.raises(RuntimeError):
        cli._sync_task(task)
    (report,) = [
        json.loads(line) for line in (home / "posted.jsonl").read_text().splitlines()
    ]
    assert report["path"] == f"/topics/{os.environ['CHEESE_TOPIC']}/messages"
    assert report["token"] == "test-secret"
    assert tasks[task]["branch"] in report["body"]["content"]
    assert report["body"]["request_id"]


def test_a_push_that_lands_tells_the_room_nothing(device):
    cli, tasks, _remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / "done.txt").write_text("done")
    git(work, "add", "done.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "landed work")
    cli._sync_task(task)
    assert not (home / "posted.jsonl").exists()


def test_rejected_concurrent_push_preserves_both_histories(device, tmp_path):
    cli, tasks, remote, home = device
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
    (bundle,) = (home / "backups" / task).glob("*.bundle")
    git(other, "fetch", str(bundle), f"refs/cheese/snapshots/{task}")
    assert git(other, "rev-parse", "FETCH_HEAD") == local_head
    assert git(other, "show", "FETCH_HEAD:local.txt") == "local writer"
    assert not git(remote, "for-each-ref", "refs/cheese/snapshots")


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


def test_a_closed_task_checkout_moved_off_its_branch_is_backed_up_not_reported(
    device,
):
    """After a task closes, its checkout is only backed up, never pushed, so the
    branch it has checked out is no longer the task's business: an agent that
    reused the checkout on another branch left work to keep, not a failed push
    to warn the room about at every turn."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    tasks[task]["closed"] = True
    git(work, "checkout", "-q", "--detach")
    (work / "leftover.txt").write_text("written after the task closed\n")

    cli._sync_all_tasks()

    assert not (home / "posted.jsonl").exists()
    (bundle,) = (home / "backups" / task).glob("*.bundle")
    recovered = home / "recovered"
    git(home, "clone", str(remote), str(recovered))
    git(recovered, "fetch", str(bundle), f"refs/cheese/snapshots/{task}")
    assert (
        git(recovered, "show", "FETCH_HEAD:leftover.txt")
        == "written after the task closed"
    )


def test_files_already_backed_up_are_not_sent_again(device, monkeypatch, tmp_path):
    """A closed task's leftover files are backed up once. Every sync after that
    finds them again, and a room switch waits on every sync: one that finds
    them unchanged sends nothing, and one that finds them changed sends the
    new files, which are what a recovery then gets."""
    cli, tasks, _remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    tasks[task]["closed"] = True
    git(work, "checkout", "-q", "--detach")
    (work / "leftover.txt").write_text("first\n")

    def uploads():
        return (home / "requests.log").read_text().count("PUT ")

    cli._sync_all_tasks()
    cli._sync_all_tasks()
    assert uploads() == 1

    (work / "leftover.txt").write_text("second\n")
    cli._sync_all_tasks()
    assert uploads() == 2

    elsewhere = tmp_path / "another-machine"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    cli._recover_task(task)
    (recovered,) = (elsewhere / ".cheese" / "recovered").iterdir()
    assert (recovered / "leftover.txt").read_text() == "second\n"


def test_a_backup_refused_once_is_rebuilt_against_the_base_it_has_now(device):
    """A backup holds only what the task's base lacks. When one is refused for
    its size and the large commit then lands on the base, the next try must
    send what is missing now, not the refused backup again at every turn."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (home / "upload-limit").write_text(str(256 * 1024))
    (work / "large.bin").write_bytes(os.urandom(512 * 1024))
    git(work, "add", "large.bin")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "large file")
    large = git(work, "rev-parse", "HEAD")
    (work / "small.txt").write_text("small\n")
    git(work, "add", "small.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "small file")

    with pytest.raises(urllib.error.HTTPError):
        cli._sync_task(task)

    # The large commit reaches the base another way, and the checkout sees it.
    git(work, "push", "-q", "origin", f"{large}:refs/heads/main")
    git(work, "fetch", "-q", "origin", "main:refs/remotes/origin/main")

    cli._sync_task(task)

    head = git(work, "rev-parse", "HEAD")
    assert git(remote, "rev-parse", tasks[task]["branch"]) == head
    (bundle,) = (home / "backups" / task).glob("*.bundle")
    recovered = home / "recovered"
    git(home, "clone", "-q", str(remote), str(recovered))
    git(recovered, "fetch", str(bundle), f"refs/cheese/snapshots/{task}")
    assert git(recovered, "rev-parse", "FETCH_HEAD") == head


def _commit(work, name, text, *extra):
    (work / name).write_text(text)
    git(work, "add", name)
    git(work, "-c", "core.hooksPath=/dev/null", "commit", *extra, "-m", f"edit {name}")
    return git(work, "rev-parse", "HEAD")


def _push_from_elsewhere(home, remote, branch, name):
    """Someone other than this checkout adds a commit to the task's branch:
    a teammate working the same task from another checkout, or a person."""
    other = home / f"elsewhere-{name}"
    git(home, "clone", "-q", "-b", branch, str(remote), str(other))
    (other / name).write_text("theirs\n")
    git(other, "add", name)
    git(
        other,
        "-c",
        "user.name=o",
        "-c",
        "user.email=o@example.com",
        "commit",
        "-m",
        f"their {name}",
    )
    git(other, "push", "-q", "origin", branch)
    return git(other, "rev-parse", "HEAD")


def test_rewriting_the_commits_this_checkout_pushed_replaces_them_on_the_branch(
    device,
):
    """Amending or rebasing what this checkout already pushed is how a task's
    commits get tidied (dropping a dependency asks for exactly that); the
    branch then carries the rewritten commits instead of refusing them."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    branch = tasks[task]["branch"]
    work = cli._task_worktree(task)
    _commit(work, "a.txt", "first\n")
    cli._sync_task(task)
    rewritten = _commit(work, "a.txt", "first, amended\n", "--amend")

    cli._sync_task(task)

    assert git(remote, "rev-parse", branch) == rewritten
    assert not (home / "posted.jsonl").exists()


def test_a_rewrite_never_drops_commits_this_checkout_never_had(device):
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    branch = tasks[task]["branch"]
    work = cli._task_worktree(task)
    _commit(work, "a.txt", "first\n")
    cli._sync_task(task)
    theirs = _push_from_elsewhere(home, remote, branch, "b.txt")
    _commit(work, "a.txt", "first, amended\n", "--amend")

    with pytest.raises(RuntimeError):
        cli._sync_task(task)

    assert git(remote, "rev-parse", branch) == theirs
    assert (home / "posted.jsonl").exists()


def test_a_checkout_behind_its_branch_has_nothing_to_push(device):
    """Another checkout of the same task went on and pushed more; this one,
    with nothing of its own since, has nothing undelivered to report."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    branch = tasks[task]["branch"]
    work = cli._task_worktree(task)
    _commit(work, "a.txt", "first\n")
    cli._sync_task(task)
    theirs = _push_from_elsewhere(home, remote, branch, "b.txt")
    git(work, "fetch", "-q", "origin")  # as agents do, to look at the branch

    cli._sync_all_tasks()
    cli._sync_all_tasks()

    assert git(remote, "rev-parse", branch) == theirs
    assert not (home / "posted.jsonl").exists()


def test_a_forge_out_of_reach_does_not_unsay_work_it_already_has(device):
    """The branch already carries this checkout's commit; the forge then stops
    answering for a while. Syncing the files still being edited backs them up
    and tells the room nothing: none of the work is missing from the branch."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    branch = tasks[task]["branch"]
    work = cli._task_worktree(task)
    pushed = _commit(work, "a.txt", "first\n")
    cli._sync_task(task)
    (work / "draft.txt").write_text("still editing\n")
    remote.rename(remote.with_name("unreachable.git"))

    cli._sync_task(task)
    cli._sync_task(task)

    assert not (home / "posted.jsonl").exists()
    assert (home / "backups" / task / "latest.json").exists()
    remote.with_name("unreachable.git").rename(remote)
    assert git(remote, "rev-parse", branch) == pushed


def test_a_new_commit_the_forge_cannot_take_is_still_reported(device):
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    _commit(work, "a.txt", "first\n")
    cli._sync_task(task)
    _commit(work, "b.txt", "second\n")
    remote.rename(remote.with_name("unreachable.git"))

    with pytest.raises(RuntimeError):
        cli._sync_task(task)

    assert tasks[task]["branch"] in (home / "posted.jsonl").read_text()


def test_commits_after_the_pr_joined_the_merge_queue_are_kept_not_pushed(device):
    """The forge locks a branch whose PR is in the merge queue. Commits made
    after that are backed up, and the room is told why they are not in the
    PR, rather than asked to retry a push that cannot land."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    branch = tasks[task]["branch"]
    work = cli._task_worktree(task)
    accepted = _commit(work, "a.txt", "accepted\n")
    cli._sync_task(task)
    tasks[task]["merge_queued_pr"] = 17

    cli._sync_all_tasks()
    assert not (home / "posted.jsonl").exists()

    later = _commit(work, "b.txt", "after the accept\n")
    with pytest.raises(RuntimeError):
        cli._sync_task(task)

    assert git(remote, "rev-parse", branch) == accepted
    (told,) = (home / "posted.jsonl").read_text().splitlines()
    assert "#17" in json.loads(told)["body"]["content"]
    bundle = home / "backups" / task / f"{later}.bundle"
    recovered = home / "recovered"
    git(home, "clone", "-q", str(remote), str(recovered))
    git(recovered, "fetch", str(bundle), f"refs/cheese/snapshots/{task}")
    assert git(recovered, "rev-parse", "FETCH_HEAD") == later


def test_a_connection_reset_once_is_tried_again_within_the_same_sync(device):
    """A reset connection is the network, not the work: the sync tries once
    more before calling the task failed and warning the room. Twice in a row
    is reported, so a request that can never go through is not retried
    forever."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / "draft.txt").write_text("unfinished\n")
    (home / "resets").write_text("1")

    cli._sync_task(task)

    assert not (home / "posted.jsonl").exists()
    (bundle,) = (home / "backups" / task).glob("*.bundle")
    recovered = home / "recovered"
    git(home, "clone", "-q", str(remote), str(recovered))
    git(recovered, "fetch", str(bundle), f"refs/cheese/snapshots/{task}")
    assert git(recovered, "show", "FETCH_HEAD:draft.txt") == "unfinished"

    (work / "draft.txt").write_text("changed again\n")
    (home / "resets").write_text("2")
    with pytest.raises(OSError):  # the reset itself, or urllib's URLError around it
        cli._sync_task(task)
    assert (home / "posted.jsonl").exists()


@pytest.mark.parametrize("left_on", ["a detached HEAD", "another branch"])
def test_a_closed_task_whose_base_branch_is_gone_is_still_backed_up(device, left_on):
    """A task stacked on another task's branch outlives that branch, which is
    deleted when it merges. The closed task's checkout, reused off its own
    branch, still holds work to keep: whatever no branch of the forge has."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    parent = "task/parent"
    git(remote, "branch", parent, "main")
    tasks[task]["base"] = parent
    work = cli._task_worktree(task)
    tasks[task]["closed"] = True
    if left_on == "a detached HEAD":
        git(work, "checkout", "-q", "--detach")
    else:
        git(work, "checkout", "-q", "-b", "elsewhere")
    _commit(work, "late.txt", "committed after the task closed\n")
    (work / "draft.txt").write_text("never committed\n")
    git(remote, "branch", "-D", parent)
    git(work, "fetch", "-q", "--prune", "origin")

    cli._sync_all_tasks()

    assert not (home / "posted.jsonl").exists()
    (bundle,) = (home / "backups" / task).glob("*.bundle")
    recovered = home / "recovered"
    git(home, "clone", "-q", str(remote), str(recovered))
    git(recovered, "fetch", str(bundle), f"refs/cheese/snapshots/{task}")
    assert git(recovered, "show", "FETCH_HEAD:late.txt") == (
        "committed after the task closed"
    )
    assert git(recovered, "show", "FETCH_HEAD:draft.txt") == "never committed"


@pytest.mark.parametrize("deleted", ["before the backup", "after the backup"])
def test_a_closed_tasks_backup_recovers_on_another_machine_once_its_base_is_gone(
    device, monkeypatch, tmp_path, deleted
):
    """A task stacked on another task's branch outlives that branch. Its backup
    holds only what the forge lacks, and recovering it on a machine that never
    had the task must not need the deleted branch: the forge still has its
    commits (a merged PR keeps its head), just under no branch name."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    parent = "task/parent"
    git(remote, "branch", parent, "main")
    below = _push_from_elsewhere(home, remote, parent, "parent.txt")
    git(remote, "update-ref", "refs/pull/1/head", below)
    git(remote, "branch", "-f", tasks[task]["branch"], parent)
    tasks[task]["base"] = parent
    work = cli._task_worktree(task)
    tasks[task]["closed"] = True
    _commit(work, "late.txt", "committed after the task closed\n")
    (work / "draft.txt").write_text("never committed\n")
    if deleted == "before the backup":
        git(remote, "branch", "-D", parent)
        git(work, "fetch", "-q", "--prune", "origin")
        cli._sync_all_tasks()
    else:
        cli._sync_all_tasks()
        git(remote, "branch", "-D", parent)
        git(remote, "branch", "-D", tasks[task]["branch"])
    assert not (home / "posted.jsonl").exists()

    elsewhere = tmp_path / "another-machine"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    cli._recover_task(task)

    (recovered,) = (elsewhere / ".cheese" / "recovered").iterdir()
    assert (recovered / "late.txt").read_text() == "committed after the task closed\n"
    assert (recovered / "draft.txt").read_text() == "never committed\n"
    assert (recovered / "parent.txt").read_text() == "theirs\n"


def test_sync_all_names_a_closed_task_it_could_not_back_up_as_closed(device, capsys):
    """The platform weighs a closed task's failed backup differently from an
    open task's failed push when a room leaves this machine, so the line that
    names the task says which of the two it is."""
    cli, tasks, _remote, home = device
    closed, open_task = tasks
    for task in tasks:
        (cli._task_worktree(task) / "draft.txt").write_text(f"{task}\n")
    tasks[closed]["closed"] = True
    (home / "upload-limit").write_text("1")

    with pytest.raises(SystemExit):
        cli._sync_all_tasks()

    printed = capsys.readouterr().err
    assert f"[cheese] 已结束的任务 {closed} 同步失败：" in printed
    assert f"[cheese] 任务 {open_task} 同步失败：" in printed


def test_a_closed_tasks_failed_backup_leaves_the_room_notice_to_the_platform(
    device,
):
    """A closed task has no turn whose work went missing. When its backup
    fails as a room leaves this machine, the platform tells the room which
    task stayed behind and why; a second message from the machine saying the
    turn's work was not pushed would only repeat it, and wrongly."""
    cli, tasks, _remote, home = device
    closed, open_task = tasks
    for task in tasks:
        (cli._task_worktree(task) / "draft.txt").write_text(f"{task}\n")
    tasks[closed]["closed"] = True
    (home / "upload-limit").write_text("1")

    with pytest.raises(SystemExit):
        cli._sync_all_tasks()

    told = [
        json.loads(line)["body"]["content"]
        for line in (home / "posted.jsonl").read_text().splitlines()
    ]
    assert len(told) == 1
    assert open_task in told[0]
    assert closed not in told[0]


def test_a_backup_leaves_out_generated_and_oversized_untracked_files_and_says_so(
    device, monkeypatch, tmp_path, capsys
):
    """Build output a repository does not ignore would otherwise ride along in
    every backup of the task. An edit to a tracked file is work however large,
    and so is a file the agent staged; what is left out is said to the agent
    that synced and to whoever recovers the backup."""
    cli, tasks, _remote, _home = device
    monkeypatch.setattr(cli, "_UNTRACKED_BACKUP_BUDGET", 1000)
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / "same.txt").write_text("tracked edit\n" * 200)
    (work / "staged.bin").write_bytes(b"s" * 2000)
    git(work, "add", "staged.bin")
    (work / "notes.txt").write_text("new, small\n")
    (work / "dist").mkdir()
    (work / "dist" / "bundle.js").write_bytes(b"x" * 3000)
    (work / "node_modules" / "pkg").mkdir(parents=True)
    (work / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n")

    monkeypatch.setattr(sys, "argv", ["cheese", "sync", "--task", task])
    cli.main()
    told_agent = capsys.readouterr().err
    assert "dist/bundle.js" in told_agent
    assert "node_modules/" in told_agent

    elsewhere = tmp_path / "another-machine"
    elsewhere.mkdir()
    monkeypatch.setenv("HOME", str(elsewhere))
    cli._recover_task(task)
    (recovered,) = (elsewhere / ".cheese" / "recovered").iterdir()
    assert (recovered / "same.txt").read_text() == "tracked edit\n" * 200
    assert (recovered / "staged.bin").read_bytes() == b"s" * 2000
    assert (recovered / "notes.txt").read_text() == "new, small\n"
    assert not (recovered / "dist").exists()
    assert not (recovered / "node_modules").exists()
    told_recoverer = capsys.readouterr().out
    assert "dist/bundle.js" in told_recoverer
    assert "node_modules/" in told_recoverer


def test_the_room_hears_once_of_each_large_file_a_backup_leaves_behind(
    device, monkeypatch, capsys
):
    """A large file nobody committed may be hours of work, and it goes with the
    machine, so the room is told. A generated directory comes back with an
    install, and a file the room has already heard of is not news at every
    sync. The push before a switch stays silent about either: the platform
    reads everything it prints as a failure."""
    cli, tasks, _remote, home = device
    monkeypatch.setattr(cli, "_UNTRACKED_BACKUP_BUDGET", 1000)
    task = next(iter(tasks))
    work = cli._task_worktree(task)

    def told():
        posted = home / "posted.jsonl"
        if not posted.exists():
            return []
        return [
            json.loads(line)["body"]["content"]
            for line in posted.read_text().splitlines()
        ]

    (work / "node_modules").mkdir()
    (work / "node_modules" / "big.js").write_bytes(b"x" * 5000)
    cli._sync_all_tasks()
    assert told() == []
    assert "PUT " not in (home / "requests.log").read_text()

    (work / "model.bin").write_bytes(b"m" * 3000)
    cli._sync_all_tasks()
    cli._sync_all_tasks()
    (work / "export.bin").write_bytes(b"e" * 4000)
    cli._sync_all_tasks()

    first, second = told()
    assert "model.bin" in first
    assert tasks[task]["branch"] in first
    assert "export.bin" in second
    assert "model.bin" not in second
    assert capsys.readouterr().err == ""
