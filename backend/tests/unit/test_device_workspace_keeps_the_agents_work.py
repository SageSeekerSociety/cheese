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
            "remote": str(remote),
            "coauthors": [],
        }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path.startswith("/topics/"):
                if (home / "reject-messages").exists():
                    # A room that is there but will not take the message: the
                    # machine has to have somewhere else to put it.
                    self.send_response(500)
                    self.end_headers()
                    return
                # What the room is told, kept where a test can read it.
                with (home / "posted.jsonl").open("a") as posted:
                    posted.write(
                        json.dumps(
                            {
                                "path": self.path,
                                "token": self.headers["Authorization"].removeprefix(
                                    "Bearer "
                                ),
                                "turn": self.headers.get("X-Cheese-Turn"),
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

        def do_GET(self):
            # The task the CLI is working. There is no other route: a sync
            # delivers commits to the forge and uploads nothing, and there is
            # nothing for a fresh sandbox to download.
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
        "CHEESE_TURN": str(uuid.uuid4()),
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


def test_a_task_worked_in_its_own_conversation_opens_and_delivers(device, monkeypatch):
    # A task is a conversation of its own: the session working it is told the
    # task's id, not its channel's, and is the one session meant to open it.
    _, tasks, remote, _ = device
    task = next(iter(tasks))
    monkeypatch.setenv("CHEESE_TOPIC", task)
    loader = SourceFileLoader("task_session_cli", str(CLI))
    cli = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(loader.name, loader)
    )
    loader.exec_module(cli)

    work = cli._task_worktree(task)
    (work / "same.txt").write_text("worked\n")
    git(work, "add", "same.txt")
    git(work, "commit", "-m", "fix: the task's own work")

    assert git(remote, "show", tasks[task]["branch"] + ":same.txt") == "worked"


def test_another_worktree_beside_a_task_commits_and_leaves_the_task_alone(
    device, tmp_path
):
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    branch = tasks[task]["branch"]
    delivered = git(remote, "rev-parse", branch)
    extra = tmp_path / "experiment"
    git(work, "worktree", "add", "-b", "experiment", str(extra), "origin/main")
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


def test_sync_leaves_uncommitted_work_alone_without_changing_index_or_branch(device):
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
    assert (work / "same.txt").read_text() == "unstaged\n"
    assert git(remote, "rev-parse", tasks[task]["branch"]) == head
    # Only pushed commits travel now: what the agent has not committed is
    # uploaded nowhere.
    assert "PUT " not in (home / "requests.log").read_text()
    assert not (home / "backups").exists()


def test_sync_never_uploads_uncommitted_files(device):
    """A checkpoint delivers commits to the forge and nothing else: the
    platform keeps no copy of the checkout's files."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    committed = _commit(work, "a.txt", "committed\n")

    cli._sync_task(task)
    cli._sync_task(task)

    assert git(remote, "rev-parse", tasks[task]["branch"]) == committed
    assert "PUT " not in (home / "requests.log").read_text()
    assert not (home / "backups").exists()


def test_sync_all_touches_only_the_tasks_with_something_to_push(device):
    """A room keeps every task it ever opened, so the push before a switch (and
    at every Stop) has to cost what is unpushed, not how old the room is. A
    file nobody committed is not delivered, so it is not work either."""
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

    cli._sync_all_tasks()

    assert git(remote, "rev-parse", tasks[committed]["branch"]) == git(
        work, "rev-parse", "HEAD"
    )
    seen = (home / "requests.log").read_text()
    assert committed in seen
    assert edited not in seen
    assert not [task for task in finished if task in seen]
    assert "PUT " not in seen


def _sync_by_hand(work, *args):
    return subprocess.run(
        [sys.executable, str(CLI), "sync", *args],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a_sync_by_hand_after_the_commits_own_is_done_without_asking_anyone(device):
    """Agents often run `cheese sync` right after `git commit`, whose hook has
    just delivered the commit. With nothing new it is done once the platform
    says the task is still on that branch: nothing is uploaded after it, and
    there is nothing to say."""
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / "a.txt").write_text("done\n")
    git(work, "add", "a.txt")
    git(work, "commit", "-m", "work")
    assert git(remote, "rev-parse", tasks[task]["branch"]) == git(
        work, "rev-parse", "HEAD"
    )
    (home / "requests.log").unlink()

    done = _sync_by_hand(work)

    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    assert (home / "requests.log").read_text().splitlines() == [
        f"GET /projects/{os.environ['CHEESE_PROJECT']}/git/tasks/{task}"
    ]


def test_a_sync_by_hand_after_a_step_landed_delivers_on_the_next_branch(
    device, tmp_path
):
    """The hand-off (`cheese_card(action=request)`) syncs the task this way. A
    checkout whose every commit is on the branch of a step that has since
    landed still has work for the next one: the sync carries it over and
    delivers it there."""
    cli, tasks, remote, _ = device
    task = next(iter(tasks))
    info = tasks[task]
    work = cli._task_worktree(task)
    (work / "same.txt").write_text("step one\n")
    git(work, "add", "same.txt")
    git(work, "commit", "-m", "feat: step one")
    earlier = info["branch"]
    _land(remote, tmp_path, info, 1)
    _commit(work, "later.txt", "step two\n")
    git(work, "push", "-q", "origin", f"HEAD:refs/heads/{earlier}")
    git(work, "fetch", "-q", "origin", f"{earlier}:refs/remotes/origin/{earlier}")

    done = _sync_by_hand(work, "--task", task)

    assert done.returncode == 0, done.stderr
    assert git(work, "symbolic-ref", "--short", "HEAD") == info["branch"]
    assert git(remote, "log", "-1", "--format=%s", info["branch"]) == "edit later.txt"


@pytest.mark.parametrize("left", ["a commit not pushed", "a file not committed"])
def test_a_sync_by_hand_delivers_commits_and_uploads_nothing(device, left):
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    delivered = git(remote, "rev-parse", tasks[task]["branch"])
    if left == "a commit not pushed":
        _commit(work, "a.txt", "not pushed\n")
    else:
        (work / "draft.txt").write_text("not committed\n")
    (home / "requests.log").unlink()

    done = _sync_by_hand(work, "--task", task)

    assert done.returncode == 0, done.stderr
    if left == "a commit not pushed":
        assert git(remote, "rev-parse", tasks[task]["branch"]) == git(
            work, "rev-parse", "HEAD"
        )
    else:
        assert git(remote, "rev-parse", tasks[task]["branch"]) == delivered
        assert (work / "draft.txt").read_text() == "not committed\n"
    assert "PUT " not in (home / "requests.log").read_text()
    assert not (home / "backups").exists()


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


def _land(remote, tmp, info, step):
    """The forge squash-merges the task's branch into main, and the platform
    moves the task onto its next step's branch."""
    landed = git(remote, "rev-parse", info["branch"])
    land = tmp / "land"
    if not land.exists():
        git(tmp, "clone", "-q", str(remote), str(land))
        git(land, "config", "user.name", "forge")
        git(land, "config", "user.email", "forge@example.com")
    git(land, "fetch", "-q", "origin")
    git(land, "checkout", "-q", "-B", "main", "origin/main")
    git(land, "merge", "-q", "--squash", "origin/" + info["branch"])
    git(land, "commit", "-q", "-m", f"step {step} (#4{step})")
    git(land, "push", "-q", "origin", "main")
    info["branch"] = info["branch"].split("-")[0] + f"-{step + 1}"
    info["landed"] = landed


def test_a_step_that_landed_moves_the_task_onto_the_latest_code(device, tmp_path):
    # A task delivering in steps goes on from the project's latest code: what
    # had not landed comes along, and the landed step is not delivered twice.
    cli, tasks, remote, _ = device
    task = next(iter(tasks))
    info = tasks[task]
    work = cli._task_worktree(task)
    (work / "same.txt").write_text("step one\n")
    git(work, "add", "same.txt")
    git(work, "commit", "-m", "feat: step one")
    _land(remote, tmp_path, info, 1)
    (work / "later.txt").write_text("step two\n")
    git(work, "add", "later.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "feat: step two")
    (work / "draft.txt").write_text("unfinished")

    assert cli._task_worktree(task) == work

    assert git(work, "symbolic-ref", "--short", "HEAD") == info["branch"]
    assert git(work, "log", "--format=%s", "origin/main..HEAD") == "feat: step two"
    assert (work / "same.txt").read_text() == "step one\n"
    assert (work / "draft.txt").read_text() == "unfinished"
    (work / "draft.txt").write_text("done")
    git(work, "add", "draft.txt")
    git(work, "commit", "-m", "feat: finish step two")
    delivered = git(remote, "log", "--format=%s", f"main..{info['branch']}")
    assert delivered.splitlines() == ["feat: finish step two", "feat: step two"]


def test_a_checkout_whose_unlanded_work_conflicts_stays_where_it_was(device, tmp_path):
    cli, tasks, remote, _ = device
    task = next(iter(tasks))
    info = tasks[task]
    work = cli._task_worktree(task)
    (work / "same.txt").write_text("step one\n")
    git(work, "add", "same.txt")
    git(work, "commit", "-m", "feat: step one")
    earlier = info["branch"]
    _land(remote, tmp_path, info, 1)
    # Someone else changes the same line on main meanwhile.
    land = tmp_path / "land"
    (land / "same.txt").write_text("theirs\n")
    git(land, "commit", "-qam", "other work")
    git(land, "push", "-q", "origin", "main")
    (work / "same.txt").write_text("mine\n")

    with pytest.raises(SystemExit):
        cli._task_worktree(task)

    assert git(work, "symbolic-ref", "--short", "HEAD") == earlier
    assert (work / "same.txt").read_text() == "mine\n"


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


def test_failed_push_is_shown_in_the_site(device):
    """A failed push is a line in 现场, not a chat message.

    Nobody in the room can retry the turn, and the message that said to read
    like one for a person. It goes to this session's own conversation, naming
    the branch and the turn the checkpoint ended — a sibling task's failure
    cannot be written into that task's 现场 with this credential."""
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
    assert report["path"] == f"/topics/{os.environ['CHEESE_TOPIC']}/session-records"
    assert report["token"] == "test-secret"
    # The turn the checkpoint ended travels with the sync, so the failure is
    # filed under that turn's 过程 rather than guessed when it is written.
    assert report["turn"] == os.environ["CHEESE_TURN"]
    assert report["body"]["subtype"] == "sync_failed"
    assert tasks[task]["branch"] in report["body"]["text"]


def test_a_push_that_lands_tells_the_room_nothing(device):
    cli, tasks, _remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / "done.txt").write_text("done")
    git(work, "add", "done.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "landed work")
    cli._sync_task(task)
    assert not (home / "posted.jsonl").exists()


def _told_the_room(home):
    """What was posted, whichever door it went through: a chat message carries
    `content`, a 现场 record carries `text`."""
    posted = home / "posted.jsonl"
    if not posted.exists():
        return []
    told = []
    for line in posted.read_text().splitlines():
        body = json.loads(line)["body"]
        told.append(body.get("content") or body.get("text"))
    return told


def test_the_pre_push_report_of_a_commit_reaches_the_room(device):
    """The report the project's entry point prints has no terminal on this path.

    An agent's commit is delivered by the post-commit hook, so whatever the
    push's own check said is said to a closed pipe unless the delivery push
    goes and reads it back.
    """
    cli, tasks, _remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    entry = work / ".cheese/pre-push"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "#!/bin/sh\n"
        'echo "ruff 报了一个错"\n'
        'echo "ruff 报了一个错" > "$CI_FAST_PRE_PUSH_REPORT"\n'
        "exit 1\n"
    )
    entry.chmod(0o755)
    (work / "work.txt").write_text("work\n")
    git(work, "add", "work.txt")
    git(work, "commit", "-m", "feat: work")

    assert [text for text in _told_the_room(home) if "ruff 报了一个错" in text]


def test_a_push_the_forge_refuses_still_reports_its_pre_push(device):
    """The report is written before the forge gets a say.

    Git runs the pre-push hook, and the check writes its report, before the
    remote can refuse the push. So the run whose red the author most needs is
    exactly the one that a read-back taken only after the push returned would
    throw away — and the sync that would carry it forward next time is the one
    that just failed.
    """
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    entry = work / ".cheese/pre-push"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "#!/bin/sh\n"
        'echo "ruff 报了一个错"\n'
        'echo "ruff 报了一个错" > "$CI_FAST_PRE_PUSH_REPORT"\n'
        "exit 1\n"
    )
    entry.chmod(0o755)
    (work / "work.txt").write_text("work\n")
    git(work, "add", "work.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "feat: work")
    reject = remote / "hooks/pre-receive"
    reject.write_text("#!/bin/sh\nexit 1\n")
    reject.chmod(0o755)

    with pytest.raises(RuntimeError):
        cli._sync_task(task)

    assert [text for text in _told_the_room(home) if "ruff 报了一个错" in text]


def test_a_report_is_not_repeated_by_the_next_push(device):
    """A report belongs to the run that wrote it.

    The file survives the sync that read it, so a later push that passes and
    therefore writes nothing must not find the last one's findings still
    sitting there — the room would be told twice, the second time about a
    push that had no findings at all.
    """
    cli, tasks, _remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    entry = work / ".cheese/pre-push"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "#!/bin/sh\n"
        'echo "第一次没过"\n'
        'echo "第一次没过" > "$CI_FAST_PRE_PUSH_REPORT"\n'
        "exit 1\n"
    )
    entry.chmod(0o755)
    (work / "one.txt").write_text("one\n")
    git(work, "add", "one.txt")
    git(work, "commit", "-m", "feat: one")
    entry.write_text("#!/bin/sh\necho '这次都过了'\nexit 0\n")
    (work / "two.txt").write_text("two\n")
    git(work, "add", "two.txt")
    git(work, "commit", "-m", "feat: two")

    assert len([t for t in _told_the_room(home) if "第一次没过" in t]) == 1


def test_a_room_that_will_not_take_the_report_gets_it_on_stderr(device, capsys):
    """The commit's own terminal is the reader that never goes away.

    `cheese sync` is reached from the commit's post-commit hook, so its stderr
    is what the person who made the commit sees. When the room cannot be told,
    the report goes there instead of being dropped.
    """
    cli, tasks, _remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    entry = work / ".cheese/pre-push"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "#!/bin/sh\n"
        'echo "ruff 报了一个错"\n'
        'echo "ruff 报了一个错" > "$CI_FAST_PRE_PUSH_REPORT"\n'
        "exit 1\n"
    )
    entry.chmod(0o755)
    (work / "work.txt").write_text("work\n")
    git(work, "add", "work.txt")
    git(work, "-c", "core.hooksPath=/dev/null", "commit", "-m", "feat: work")
    (home / "reject-messages").write_text("")

    cli._sync_task(task)

    assert "ruff 报了一个错" in capsys.readouterr().err
    assert not [t for t in _told_the_room(home) if "ruff 报了一个错" in t]


def test_a_push_killed_for_outrunning_its_budget_still_reports(
    device, tmp_path, monkeypatch
):
    """The report is the only copy a killed push has, so it must survive one.

    Git runs `.cheese/pre-push` inside the push, and that script is allowed a
    wall clock of its own — 240 s by default — so the two budgets are one and a
    push that outruns the total is killed with the hook's report already on
    disk. The read-back runs in a `finally` for exactly that case.
    """
    cli, _tasks, _remote, _home = device
    work = tmp_path / "work"
    work.mkdir()
    report = work / "report.txt"
    budgets = []

    class Ran:
        returncode = 0
        stdout = ""
        stderr = ""

    def outrun(args, **kwargs):
        budgets.append(kwargs["timeout"])
        if args[-1] == "push":
            # The hook runs, leaves its report, and then the push itself
            # outruns the budget it shares with the hook.
            report.write_text("ruff 报了一个错\n")
            raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs["timeout"])
        return Ran()

    cli._pending_pre_push_reports.clear()
    monkeypatch.setattr(cli.subprocess, "run", outrun)
    cli._git_task(work, "rev-parse", "--git-dir")
    with pytest.raises(subprocess.TimeoutExpired):
        cli._git_task(work, "push", pre_push_report=report)

    assert list(cli._pending_pre_push_reports.values()) == ["ruff 报了一个错"]
    # A push that runs the hook needs more room than the calls that do not:
    # the hook's own 240 s and the transfer do not fit in the plain budget.
    assert budgets[1] > budgets[0]


def test_a_retried_push_that_passed_forgets_the_attempt_before_it(
    device, tmp_path, monkeypatch
):
    """The room hears the push that landed, not the one that was thrown away.

    `_once_more_if_transient` re-runs a delivery on a flaky link, and each
    attempt unlinks the report file before its own push. An attempt whose hook
    passes leaves no file, so the read-back has nothing for it — and nothing is
    what it has to say: the text the attempt before it left is dropped, rather
    than posted as the outcome of a push whose check passed.
    """
    cli, _tasks, _remote, _home = device
    work = tmp_path / "work"
    work.mkdir()
    report = work / "report.txt"
    pushes = []

    class Ran:
        returncode = 0
        stdout = ""
        stderr = ""

    def first_failed(args, **_kwargs):
        if args[-1] == "push":
            pushes.append(1)
            if len(pushes) == 1:
                report.write_text("ruff 报了一个错\n")
        return Ran()

    cli._pending_pre_push_reports.clear()
    monkeypatch.setattr(cli.subprocess, "run", first_failed)
    cli._git_task(work, "push", pre_push_report=report)
    assert list(cli._pending_pre_push_reports.values()) == ["ruff 报了一个错"]

    # The retry: its own hook runs, passes, and writes nothing.
    cli._git_task(work, "push", pre_push_report=report)
    assert list(cli._pending_pre_push_reports.values()) == []


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
    # The refused commit stays in the checkout that made it; nothing else
    # carries it now.
    assert git(work, "rev-parse", "HEAD") == local_head
    assert git(work, "show", "HEAD:local.txt") == "local writer"
    assert "PUT " not in (home / "requests.log").read_text()
    assert not (home / "backups").exists()


def test_closed_task_sync_leaves_its_files_and_delivered_branch_alone(device):
    cli, tasks, remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    head = git(remote, "rev-parse", tasks[task]["branch"])
    tasks[task]["closed"] = True
    (work / "draft.txt").write_text("late work")
    cli._sync_task(task)
    assert git(remote, "rev-parse", tasks[task]["branch"]) == head
    assert (work / "draft.txt").read_text() == "late work"
    assert "PUT " not in (home / "requests.log").read_text()
    assert not (home / "backups").exists()
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
        (seed / directory / "uv.lock").write_text("1")
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
            "printf '#!/bin/sh\\ncat uv.lock\\n' "
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
    (first_work / "backend/uv.lock").write_text("2")
    (first_work / "draft.txt").write_text("unfinished work")
    assert open_task(first).returncode == 0
    assert (first_work / "backend/installed-version").read_text() == "2"
    assert (second_work / "backend/installed-version").read_text() == "1"
    assert (first_work / "startup-count").read_text() == "startup\nstartup\n"

    (first_work / "fail-install").touch()
    (first_work / "backend/uv.lock").write_text("3")
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
    assert (first_work / "backend/installed-version").read_text() == "3"
    assert (home / "setup-count").read_text() == "setup\n"
    assert (first_work / "draft.txt").read_text() == "unfinished work"


def _prepared_room(home, startup_script):
    """A room whose environment runner prepares each task checkout with
    `startup_script`, as a machine the platform launched has it."""
    room = home / "room"
    room.mkdir()
    helpers = home / ".cheese"
    helpers.mkdir(exist_ok=True)
    shutil.copy(environment_runner.__file__, helpers / "cheese-environment.py")
    config = EnvironmentConfig(startup_script=startup_script)
    started = subprocess.run(
        [sys.executable, environment_runner.__file__, "true"],
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


def test_reopening_a_task_left_as_it_was_needs_neither_the_forge_nor_an_install(
    device,
):
    """An agent's shell starts each command in the room again, so it opens the
    task again for nearly every command it runs there. A checkout already on the
    task's branch, whose dependency files are the ones last installed from,
    comes back as it is: with the forge out of reach, and without the startup
    script running again."""
    cli, tasks, remote, home = device
    _prepared_room(home, "echo startup >> startup-count")
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / "draft.txt").write_text("unfinished\n")
    _commit(work, "a.txt", "committed\n")
    remote.rename(remote.with_name("unreachable.git"))

    assert cli._task_worktree(task) == work
    assert cli._task_worktree(task) == work

    assert (work / "startup-count").read_text() == "startup\n"
    assert (work / "draft.txt").read_text() == "unfinished\n"


@pytest.mark.parametrize(
    "change",
    ["a lock file edited", "a dependency file added", "a branch with other locks"],
)
def test_a_reopen_installs_again_once_the_dependency_files_change(device, change):
    cli, tasks, _, home = device
    _prepared_room(
        home, "cat uv.lock >> installed 2>/dev/null || echo none >> installed"
    )
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    if change == "a dependency file added":
        (work / "uv.lock").write_text("added\n")
        wanted = "none\nadded\n"
    else:
        (work / "uv.lock").write_text("first\n")
        _commit(work, "uv.lock", "first\n")
        assert cli._task_worktree(task) == work
        if change == "a lock file edited":
            (work / "uv.lock").write_text("second\n")
            wanted = "none\nfirst\nsecond\n"
        else:
            git(work, "reset", "-q", "--hard", "HEAD~1")
            wanted = "none\nfirst\nnone\n"

    assert cli._task_worktree(task) == work

    assert (work / "installed").read_text() == wanted


@pytest.mark.parametrize(
    "manifest",
    ["requirements/base.txt", "setup.cfg", "environment.yml", "go.mod", "Gemfile"],
)
def test_a_reopen_installs_again_once_a_manifest_changes(device, manifest):
    """Projects name their dependencies in many places; a hosted repository
    does not rename its files for the platform."""
    cli, tasks, _, home = device
    _prepared_room(home, f"cat {manifest} >> installed 2>/dev/null || true")
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / manifest).parent.mkdir(parents=True, exist_ok=True)
    _commit(work, manifest, "first\n")
    # The first open had nothing to read yet; this one installs from it.
    (work / "installed").unlink(missing_ok=True)
    cli._task_worktree(task)
    (work / manifest).write_text("second\n")

    cli._task_worktree(task)

    assert (work / "installed").read_text() == "first\nsecond\n"


@pytest.mark.parametrize("how", ["removed by hand", "cleaned by git"])
def test_a_reopen_installs_again_once_what_it_installed_is_gone(device, how):
    cli, tasks, _, home = device
    _prepared_room(home, "mkdir -p .venv && echo ran >> .venv/../installs")
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / ".gitignore").write_text(".venv/\ninstalls\n")
    _commit(work, ".gitignore", ".venv/\ninstalls\n")
    assert cli._task_worktree(task) == work
    runs = (work / "installs").read_text()
    if how == "removed by hand":
        shutil.rmtree(work / ".venv")
    else:
        git(work, "clean", "-q", "-x", "-d", "-f", "-e", "installs")

    cli._task_worktree(task)

    assert (work / "installs").read_text() == runs + "ran\n"
    assert (work / ".venv").is_dir()


def test_a_reopen_installs_again_once_the_room_has_a_new_environment(device):
    cli, tasks, _, home = device
    _prepared_room(home, "echo ran >> installs")
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    config = home / ".cheese-environment/config.json"
    pinned = json.loads(config.read_text())
    config.write_text(json.dumps({**pinned, "startup_script": "echo new >> installs"}))

    cli._task_worktree(task)

    assert (work / "installs").read_text() == "ran\nnew\n"


def test_a_reopen_after_a_failed_install_installs_again(device):
    """An install that failed partway leaves the checkout half prepared, even
    once its dependency files are back as they were when it last succeeded."""
    cli, tasks, _, home = device
    _prepared_room(
        home,
        "echo ran >> installs\ntest ! -f fail-install",
    )
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / "uv.lock").write_text("broken\n")
    (work / "fail-install").touch()
    with pytest.raises(SystemExit):
        cli._task_worktree(task)
    (work / "uv.lock").unlink()
    (work / "fail-install").unlink()
    before = (work / "installs").read_text()

    cli._task_worktree(task)

    assert (work / "installs").read_text() == before + "ran\n"


def test_a_reopen_after_an_open_killed_mid_install_installs_again(device):
    """An open killed while it installs (the machine reclaimed, a timeout)
    never gets to say the install failed."""
    cli, tasks, _, home = device
    _prepared_room(
        home,
        "echo ran >> installs\n"
        # The `cheese worktree` that started this install, killed outright.
        'test ! -f kill-open || kill -9 "$(ps -o ppid= -p "$PPID")"\n'
        "sleep 1",
    )
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    (work / "uv.lock").write_text("half installed\n")
    (work / "kill-open").touch()
    killed = subprocess.run(
        [sys.executable, str(CLI), "worktree", task],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert killed.returncode == -9, killed.stderr
    (work / "uv.lock").unlink()
    (work / "kill-open").unlink()
    before = (work / "installs").read_text()

    cli._task_worktree(task)

    assert (work / "installs").read_text() == before + "ran\n"


def test_a_reopen_installs_again_when_asked_to(device):
    cli, tasks, _, home = device
    _prepared_room(home, "echo ran >> installs")
    task = next(iter(tasks))
    work = cli._task_worktree(task)

    opened = subprocess.run(
        [sys.executable, str(CLI), "worktree", task, "--prepare"],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert opened.returncode == 0, opened.stderr
    assert opened.stdout.strip() == str(work)
    assert (work / "installs").read_text() == "ran\nran\n"


def test_a_reopen_follows_the_task_to_its_new_forge(device, tmp_path):
    """A project moved to another forge: the reopen reads the task's base
    from there, not from what the old one last had."""
    cli, tasks, remote, _ = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    moved = tmp_path / "moved.git"
    git(tmp_path, "clone", "-q", "--bare", str(remote), str(moved))
    remote.rename(remote.with_name("retired.git"))
    tasks[task]["remote"] = str(moved)
    elsewhere = tmp_path / "elsewhere"
    git(tmp_path, "clone", "-q", str(moved), str(elsewhere))
    git(elsewhere, "config", "user.name", "someone")
    git(elsewhere, "config", "user.email", "someone@example.com")
    _commit(elsewhere, "new.txt", "landed on the new forge\n")
    git(elsewhere, "push", "-q", "origin", "HEAD:main")

    assert cli._task_worktree(task) == work

    assert git(work, "rev-parse", "origin/main") == git(moved, "rev-parse", "main")


def test_a_closed_task_checkout_moved_off_its_branch_is_left_alone_not_reported(
    device,
):
    """After a task closes, its checkout is neither pushed nor uploaded, so the
    branch it has checked out is no longer the task's business: an agent that
    reused the checkout on another branch left work on the machine, not a
    failed push to warn the room about at every turn."""
    cli, tasks, _remote, home = device
    task = next(iter(tasks))
    work = cli._task_worktree(task)
    tasks[task]["closed"] = True
    git(work, "checkout", "-q", "--detach")
    (work / "leftover.txt").write_text("written after the task closed\n")

    cli._sync_all_tasks()

    assert not (home / "posted.jsonl").exists()
    assert (work / "leftover.txt").read_text() == "written after the task closed\n"
    assert "PUT " not in (home / "requests.log").read_text()
    assert not (home / "backups").exists()


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
    answering for a while. Syncing the files still being edited uploads
    nothing and tells the room nothing: none of the work is missing from the
    branch."""
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
    assert "PUT " not in (home / "requests.log").read_text()
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
    after that stay in the checkout, and the room is told why they are not in
    the PR, rather than asked to retry a push that cannot land."""
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
    # The commit the forge would not take is still here; nothing else holds it.
    assert git(work, "rev-parse", "HEAD") == later
    assert git(work, "show", "HEAD:b.txt") == "after the accept"
    (told,) = (home / "posted.jsonl").read_text().splitlines()
    assert "#17" in json.loads(told)["body"]["content"]
    assert "PUT " not in (home / "requests.log").read_text()
