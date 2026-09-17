"""Protect original bytes and keep late subprocesses away from reused work."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

from app.domain.agent import resource_cleanup as cleanup


def test_remove_readonly_cache_preserves_symlink_target(tmp_path):
    home = tmp_path / "home"
    cache = home / "go/pkg/mod/toolchain"
    cache.mkdir(parents=True)
    (cache / "compiler").write_text("cached binary")
    outside = tmp_path / "original"
    outside.mkdir()
    (outside / "source").write_text("keep this")
    (cache / "external").symlink_to(outside, target_is_directory=True)
    cache.chmod(0o555)
    cache.parent.chmod(0o555)
    outside.chmod(0o555)
    try:
        cleanup.remove_tree(home)
        assert not home.exists()
        assert (outside / "source").read_text() == "keep this"
        assert outside.stat().st_mode & 0o777 == 0o555
    finally:
        outside.chmod(0o755)


@pytest.fixture
def terminal_resource(tmp_path):
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home, work = cleanup.resource_paths(tmp_path, project, resource)
    for name in (".claude", ".cheese"):
        (home / name).mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="cheese-cleanup-") as runtime:
        from pathlib import Path

        socket = str(Path(runtime) / "s")
        config = Path(runtime) / "tmux.conf"
        config.write_text("set -g remain-on-exit on\n")

        def tmux(*args):
            result = subprocess.run(
                ["tmux", "-S", socket, "-f", str(config), *args],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        try:
            yield home, work, socket, tmux
        finally:
            subprocess.run(["tmux", "-S", socket, "kill-server"], capture_output=True)


@pytest.mark.parametrize("connector_owned", [False, True])
def test_exited_terminal_is_closed_without_touching_prefix_neighbor(
    terminal_resource, connector_owned
):
    home, work, socket, tmux = terminal_resource
    name = (
        "screen-123"
        if connector_owned
        else "cheese_"
        + subprocess.run(
            ["cksum"], input=str(work), text=True, capture_output=True, check=True
        ).stdout.split()[0]
    )
    tmux("new-session", "-d", "-s", name, "sh", "-c", "read answer")
    tmux("new-session", "-d", "-s", name + "-neighbor", "sleep", "60")
    if connector_owned:
        tmux(
            "set-option",
            "-t",
            "=" + name + ":",
            "@cheese-screen",
            json.dumps(
                {
                    "sid": name,
                    "env": {
                        "CHEESE_PROJECT": home.parent.name,
                        "CHEESE_RESOURCE_ID": home.name,
                    },
                }
            ),
        )
    (home / ".cheese/environment-session.json").write_text(json.dumps([socket, name]))
    tmux("send-keys", "-t", "=" + name + ":", "Enter")
    wait_for(
        lambda: (
            tmux("display-message", "-p", "-t", "=" + name + ":", "#{pane_dead}") == "1"
        )
    )
    with (home / "lock").open("w") as lock:
        cleanup.request_exit(home, work, lock.fileno())
        cleanup.request_exit(home, work, lock.fileno())
    assert tmux("list-sessions", "-F", "#{session_name}") == name + "-neighbor"


def test_exit_targets_live_pane_after_the_original_pane_has_exited(terminal_resource):
    home, work, socket, tmux = terminal_resource
    name = (
        "cheese_"
        + subprocess.run(
            ["cksum"], input=str(work), text=True, capture_output=True, check=True
        ).stdout.split()[0]
    )
    tmux("new-session", "-d", "-s", name, "sh", "-c", "read answer")
    tmux("send-keys", "-t", "=" + name + ":", "Enter")
    wait_for(
        lambda: (
            tmux("display-message", "-p", "-t", "=" + name + ":", "#{pane_dead}") == "1"
        )
    )
    received = home / "received"
    tmux(
        "new-window",
        "-t",
        "=" + name + ":",
        "sh",
        "-c",
        'read answer; printf %s "$answer" > "$1"',
        "sh",
        str(received),
    )
    (home / ".cheese/environment-session.json").write_text(json.dumps([socket, name]))
    with (home / "lock").open("w") as lock:
        with pytest.raises(RuntimeError, match="waiting for the agent"):
            cleanup.request_exit(home, work, lock.fileno())
        wait_for(lambda: received.exists() and received.read_text() == "/exit")
        wait_for(
            lambda: all(
                line == "1"
                for line in tmux(
                    "list-panes", "-s", "-t", "=" + name + ":", "-F", "#{pane_dead}"
                ).splitlines()
            )
        )
        cleanup.request_exit(home, work, lock.fileno())


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("fixture did not reach the expected process state")


@pytest.mark.parametrize("task_layout", [False, True])
def test_stop_lock_survives_parent_death_and_late_retry_is_read_only(
    tmp_path, task_layout
):
    project, resource, operation = (str(uuid.uuid4()) for _ in range(3))
    home, work = cleanup.resource_paths(tmp_path, project, resource)
    for name in (".claude", ".cheese"):
        (home / name).mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True)
    socket = tmp_path / "tmux.sock"
    socket.touch()
    checksum = (
        subprocess.run(
            ["cksum"],
            input=str(home / "room" if task_layout else work).encode(),
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split()[0]
    )
    (home / ".cheese/environment-session.json").write_text(
        json.dumps([str(socket), "cheese_" + checksum])
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    tmux = binary / "tmux"
    tmux.write_text(
        f"#!{sys.executable}\n"
        + """import os, sys, time
from pathlib import Path
root = Path(os.environ["HOME"])
if "has-session" in sys.argv:
    sys.exit(1 if (root / "delivered").exists() else 0)
if "list-panes" in sys.argv:
    print("%0 0")
    sys.exit(0)
(root / "started").write_text(str(os.getpid()))
while not (root / "release").exists():
    time.sleep(0.01)
with (root / "delivered").open("a") as output:
    output.write("stop\\n")
"""
    )
    tmux.chmod(0o755)
    lsof = binary / "lsof"
    lsof.write_text("#!/bin/sh\nexit 1\n")
    lsof.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
    }
    command = [
        sys.executable,
        cleanup.__file__,
        "prepare",
        project,
        resource,
        operation,
    ]
    receipt = tmp_path / ".cheese/cleanup" / operation / (resource + ".ready")
    with (tmp_path / "process.log").open("wb") as log:
        first = subprocess.Popen(command, env=env, stdout=log, stderr=log)
        second = None
        try:
            wait_for(lambda: (tmp_path / "started").exists())
            first.kill()
            first.wait(timeout=5)
            second = subprocess.Popen(command, env=env, stdout=log, stderr=log)
            time.sleep(0.15)
            assert second.poll() is None and not receipt.exists()
            (tmp_path / "release").touch()
            assert second.wait(timeout=5) == 0
            assert receipt.read_text() == "ready\n"
            assert (tmp_path / "delivered").read_text() == "stop\n"
            # An old prepare arriving after a new session started must do nothing.
            subprocess.run(
                command, env=env, stdout=log, stderr=log, check=True, timeout=5
            )
            assert (tmp_path / "delivered").read_text() == "stop\n"
        finally:
            (tmp_path / "release").touch()
            for process in (first, second):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


def test_deletion_refuses_tail_written_after_confirmation(tmp_path):
    home = tmp_path / "home"
    original = home / ".claude/projects/p/session.jsonl"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original\n")
    receipts = [
        {
            "source": str(original.relative_to(home)),
            "size": 9,
            "sha256": hashlib.sha256(b"original\n").hexdigest(),
        }
    ]
    cleanup.check_transcripts(home, receipts)
    original.write_bytes(b"original\nlate result\n")
    with pytest.raises(RuntimeError, match="changed"):
        cleanup.check_transcripts(home, receipts)
    assert original.read_bytes().endswith(b"late result\n")


@pytest.mark.parametrize("name", ["cheese-preview", "cheese-tunnel"])
@pytest.mark.parametrize("has_executor", [False, True])
def test_resource_helpers_stop_even_without_an_executor(tmp_path, name, has_executor):
    resource = str(uuid.uuid4())
    home = tmp_path / resource
    directory = home / ".cheese"
    directory.mkdir(parents=True)
    helper = directory / (name + ".py")
    ready = directory / "ready"
    helper.write_text(
        "import sys, time\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).touch()\ntime.sleep(60)\n"
    )
    if has_executor:
        (directory / "execution-owner.json").write_text(
            json.dumps({"resource": resource})
        )
        runtime = directory / "remote-execution/runtime.py"
        runtime.parent.mkdir()
        runtime.write_text("def socket_path(state):\n    return state / 'absent'\n")
    process = subprocess.Popen([sys.executable, str(helper), str(ready)])
    unrelated = subprocess.Popen(["sleep", "60"])
    try:
        wait_for(ready.exists)
        marker = directory / (name + ".pid")
        marker.write_text(str(unrelated.pid))
        cleanup.stop_executor(home, resource)
        assert unrelated.poll() is None
        assert process.poll() is None
        marker.write_text(str(process.pid))
        cleanup.stop_executor(home, resource)
        assert process.wait(timeout=5) != 0
        assert unrelated.poll() is None
        cleanup.stop_executor(home, resource)
    finally:
        for child in (process, unrelated):
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)


def test_teardown_stops_the_executor_a_previous_root_installed(tmp_path):
    """Closing a room reads what preparing it wrote.

    A room prepared before the platform moved its own files still has its
    executor, and every marker describing it, where that launcher put them —
    nothing moves them until the room is prepared again, and a room being torn
    down never will be. Reading only the current root answers "this room never
    had an executor", and that answer is acted on: the detached daemon is left
    running under a home that is then deleted out from under it, the private
    seat it holds is never released, and publication is checked on the branch
    meant for rooms that have no executor.
    """
    resource = str(uuid.uuid4())
    home = tmp_path / resource
    previous = home / ".claude"
    (previous / "remote-execution").mkdir(parents=True)
    (previous / "executor").mkdir()
    (previous / "executor/socket").touch()
    (previous / "execution-owner.json").write_text(json.dumps({"resource": resource}))
    (previous / "remote-target.json").write_text(
        json.dumps({"kind": "device", "resource_id": resource})
    )
    asked = tmp_path / "asked-to-stop.json"
    (previous / "remote-execution/runtime.py").write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "def socket_path(state):\n"
        "    return Path(state) / 'socket'\n"
        "if __name__ == '__main__':\n"
        f"    json.dump(sys.argv[1:], open({str(asked)!r}, 'w'))\n"
    )

    # Not a room that never had one — which is what decides three branches.
    assert cleanup.session_target(home, resource)["kind"] == "device"
    cleanup.stop_executor(home, resource)

    assert json.loads(asked.read_text()) == [
        "stop",
        "--state",
        str(previous / "executor"),
    ]


def test_teardown_reads_the_current_root_when_a_room_has_moved(tmp_path):
    """A migrated room has leftovers under both. What is running decides."""
    resource = str(uuid.uuid4())
    home = tmp_path / resource
    (home / ".claude/remote-execution").mkdir(parents=True)
    for name in (".cheese", ".claude"):
        (home / name / "executor").mkdir(parents=True, exist_ok=True)
    (home / ".cheese/remote-target.json").write_text(
        json.dumps({"kind": "private", "topic": resource})
    )
    (home / ".claude/remote-target.json").write_text(
        json.dumps({"kind": "private", "topic": str(uuid.uuid4())})
    )

    assert cleanup.session_target(home, resource)["topic"] == resource


def test_unpublished_source_blocks_cleanup(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "source.py").write_text("work in progress")
    with pytest.raises(RuntimeError, match="working-tree changes"):
        cleanup.check_published(tmp_path)
    assert (tmp_path / "source.py").exists()


def test_private_cleanup_rejects_another_generation(tmp_path):
    resource = str(uuid.uuid4())
    marker = tmp_path / ".cheese/remote-target.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"kind": "private", "topic": resource}))
    assert cleanup.session_target(tmp_path, resource)["topic"] == resource
    with pytest.raises(RuntimeError, match="another resource generation"):
        cleanup.session_target(tmp_path, str(uuid.uuid4()))


def test_task_work_is_checked_before_removing_the_room_home(tmp_path):
    home, work = tmp_path / "home", tmp_path / "legacy-work"
    task = home / ".cheese/tasks" / str(uuid.uuid4())
    task.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(task)], check=True)
    (task / "source.py").write_text("unpublished task work")
    with pytest.raises(RuntimeError, match="working-tree changes"):
        cleanup.check_resource_publication(home, work)
    assert (task / "source.py").read_text() == "unpublished task work"


def test_unpublished_room_notes_block_home_removal(tmp_path):
    home, work = tmp_path / "home", tmp_path / "legacy-work"
    room = home / "room"
    room.mkdir(parents=True)
    (room / "notes.md").write_text("unfinished research")
    with pytest.raises(RuntimeError, match="no Git publication record"):
        cleanup.check_resource_publication(home, work)
    assert (room / "notes.md").read_text() == "unfinished research"


def test_bare_cache_keeps_unpublished_commits_after_checkout_is_removed(tmp_path):
    home, work = tmp_path / "home", tmp_path / "legacy-work"
    repo = home / ".cheese/repositories/project.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    for args in (
        ["init", "-q", "-b", "main"],
        [
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "unpublished work",
        ],
    ):
        subprocess.run(["git", "-C", str(seed), *args], check=True, capture_output=True)
    repo.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(repo)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(RuntimeError, match="unpublished commits"):
        cleanup.check_resource_publication(home, work)
    # A fetched origin ref is the publication evidence used by task checkouts.
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "main"],
        check=True,
        capture_output=True,
    )
    cleanup.check_resource_publication(home, work)
