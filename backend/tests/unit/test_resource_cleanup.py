"""Protect original bytes and keep late subprocesses away from reused work."""

import gzip
import hashlib
import http.server
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
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
    # A launcher stands in for the pane's program: asked to stop, it says so
    # and leaves, the way the real one stops its runner and exits.
    tmux(
        "new-window",
        "-t",
        "=" + name + ":",
        "sh",
        "-c",
        "trap 'printf TERM > \"$1\"; exit 0' TERM; while :; do sleep 0.1; done",
        "sh",
        str(received),
    )
    (home / ".cheese/environment-session.json").write_text(json.dumps([socket, name]))
    with (home / "lock").open("w") as lock:
        with pytest.raises(RuntimeError, match="waiting for the agent"):
            cleanup.request_exit(home, work, lock.fileno())
        wait_for(lambda: received.exists() and received.read_text() == "TERM")
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
    print("%0 0 4242")
    sys.exit(0)
"""
    )
    tmux.chmod(0o755)
    # The stop request is the signal to the pane's program, sent by a child
    # that holds the stop lock; this one takes its time about it.
    kill = binary / "kill"
    kill.write_text(
        f"#!{sys.executable}\n"
        + """import os, time
from pathlib import Path
root = Path(os.environ["HOME"])
(root / "started").write_text(str(os.getpid()))
while not (root / "release").exists():
    time.sleep(0.01)
with (root / "delivered").open("a") as output:
    output.write("stop\\n")
"""
    )
    kill.chmod(0o755)
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
        "-",
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


def test_a_room_that_will_not_leave_is_ended_once_the_grace_has_run_out(tmp_path):
    """An archived room whose processes ignore the polite request is ended.

    Before the grace, prepare refuses with the same "still has processes"
    it always did, and the process lives on. After it, prepare ends the
    process and completes — the process here has a cwd inside the room and
    handles nothing, the way a dev server an agent left behind does.
    """
    project, resource, operation = (str(uuid.uuid4()) for _ in range(3))
    home, work = cleanup.resource_paths(tmp_path, project, resource)
    for name in (".claude", ".cheese"):
        (home / name).mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True)
    squatter = subprocess.Popen(["sleep", "300"], cwd=work)
    command = [
        sys.executable,
        cleanup.__file__,
        "prepare",
        project,
        resource,
        operation,
        "-",
    ]
    receipt = tmp_path / ".cheese/cleanup" / operation / (resource + ".ready")
    try:
        refused = subprocess.run(
            command,
            env={
                **os.environ,
                "HOME": str(tmp_path),
                "CHEESE_CLEANUP_FORCE_AFTER_S": "3600",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert refused.returncode != 0 and "still has processes" in refused.stderr
        assert squatter.poll() is None and not receipt.exists()
        ended = subprocess.run(
            command,
            env={
                **os.environ,
                "HOME": str(tmp_path),
                "CHEESE_CLEANUP_FORCE_AFTER_S": "0",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert ended.returncode == 0, ended.stderr
        assert receipt.read_text() == "ready\n"
        assert squatter.wait(timeout=5) != 0
    finally:
        if squatter.poll() is None:
            squatter.kill()
            squatter.wait(timeout=5)


def test_a_safety_refusal_is_never_overruled_by_time(tmp_path):
    """Only "still running" ages into force; a socket of another user does not."""
    project, resource, operation = (str(uuid.uuid4()) for _ in range(3))
    home, work = cleanup.resource_paths(tmp_path, project, resource)
    for name in (".claude", ".cheese"):
        (home / name).mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True)
    (home / ".cheese/environment-session.json").write_text(
        json.dumps(["/dev/null", "cheese_0"])
    )
    refused = subprocess.run(
        [
            sys.executable,
            cleanup.__file__,
            "prepare",
            project,
            resource,
            operation,
            "-",
        ],
        env={**os.environ, "HOME": str(tmp_path), "CHEESE_CLEANUP_FORCE_AFTER_S": "0"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert refused.returncode != 0
    assert "owned by another user" in refused.stderr
    assert not (
        tmp_path / ".cheese/cleanup" / operation / (resource + ".ready")
    ).exists()


def run_cleanup(machine_home, action, project, resource, room="-"):
    return subprocess.run(
        [
            sys.executable,
            cleanup.__file__,
            action,
            project,
            resource,
            str(uuid.uuid4()),
            room,
        ],
        env={**os.environ, "HOME": str(machine_home)},
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def room_home(tmp_path):
    """A room's home on the session host, with a main and a subagent transcript."""
    project, resource, room = (str(uuid.uuid4()) for _ in range(3))
    home, _work = cleanup.resource_paths(tmp_path, project, resource)
    sessions = home / ".claude/projects/-room"
    (sessions / "77d3f6bc/subagents").mkdir(parents=True)
    (sessions / "77d3f6bc.jsonl").write_bytes(b'{"main":1}\n')
    (sessions / "77d3f6bc/subagents/agent-a.jsonl").write_bytes(b'{"sub":1}\n')
    archive = tmp_path / ".cheese/transcripts" / project / room / (resource + ".tar.gz")
    return project, resource, room, home, archive


def archived_files(archive):
    with tarfile.open(archive) as bundle:
        return {
            member.name: bundle.extractfile(member).read()
            for member in bundle.getmembers()
            if member.isfile()
        }


def test_removal_keeps_the_rooms_transcripts_compressed_on_the_host(
    tmp_path, room_home
):
    project, resource, room, home, archive = room_home
    removed = run_cleanup(tmp_path, "remove", project, resource, room)
    assert removed.returncode == 0, removed.stderr
    assert not home.exists()
    assert archive.stat().st_mode & 0o777 == 0o600
    assert archived_files(archive) == {
        "projects/-room/77d3f6bc.jsonl": b'{"main":1}\n',
        "projects/-room/77d3f6bc/subagents/agent-a.jsonl": b'{"sub":1}\n',
    }


def test_a_link_in_the_transcripts_is_kept_as_a_link_and_never_followed(
    tmp_path, room_home
):
    project, resource, room, home, archive = room_home
    outside = tmp_path / "elsewhere/secret.jsonl"
    outside.parent.mkdir()
    outside.write_bytes(b"SECRET-OUTSIDE-THE-HOME\n")
    (home / ".claude/projects/-room/linked.jsonl").symlink_to(outside)
    removed = run_cleanup(tmp_path, "remove", project, resource, room)
    assert removed.returncode == 0, removed.stderr
    assert b"SECRET-OUTSIDE-THE-HOME" not in gzip.decompress(archive.read_bytes())
    assert outside.read_bytes() == b"SECRET-OUTSIDE-THE-HOME\n"


def test_removal_on_a_device_that_keeps_no_transcripts_keeps_nothing(
    tmp_path, room_home
):
    project, resource, _room, home, _archive = room_home
    removed = run_cleanup(tmp_path, "remove", project, resource)
    assert removed.returncode == 0, removed.stderr
    assert not home.exists()
    assert not (tmp_path / ".cheese/transcripts").exists()


def test_expiry_deletes_only_that_generations_transcripts(tmp_path, room_home):
    project, resource, room, _home, archive = room_home
    assert run_cleanup(tmp_path, "remove", project, resource, room).returncode == 0
    # The same room, reopened and archived again: a second generation beside it.
    later = str(uuid.uuid4())
    later_home, _work = cleanup.resource_paths(tmp_path, project, later)
    (later_home / ".claude/projects/-room").mkdir(parents=True)
    (later_home / ".claude/projects/-room/new.jsonl").write_bytes(b"{}\n")
    assert run_cleanup(tmp_path, "remove", project, later, room).returncode == 0
    second = archive.with_name(later + ".tar.gz")
    assert second.exists()

    expired = run_cleanup(tmp_path, "expire", project, resource, room)
    assert expired.returncode == 0, expired.stderr
    assert not archive.exists()
    assert second.exists()
    assert run_cleanup(tmp_path, "expire", project, later, room).returncode == 0
    assert not (tmp_path / ".cheese/transcripts" / project).exists()
    # Expiring again, as a retried sweep would, finds nothing and succeeds.
    assert run_cleanup(tmp_path, "expire", project, later, room).returncode == 0


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


def test_a_home_restored_onto_a_fresh_host_stops_with_nothing_running(tmp_path):
    """A sandbox's home comes back from its archive with its links into the
    release it last ran from, but the fresh host never staged that release and
    the room has not been prepared there yet. Putting it to sleep must succeed:
    no executor of it can be running on this host."""
    resource = str(uuid.uuid4())
    home = tmp_path / resource
    directory = home / ".cheese"
    (directory / "remote-execution").mkdir(parents=True)
    (directory / "execution-owner.json").write_text(json.dumps({"resource": resource}))
    gone = tmp_path / ".cheese/executor-releases" / ("0" * 64)
    (directory / "remote-execution/runtime.py").symlink_to(
        gone / "remote-execution/runtime.py"
    )

    cleanup.stop_executor(home, resource)


def test_each_seats_tunnel_helper_stops_with_its_room(tmp_path):
    """A room seats several agents, each with its own tunnel helper whose pid
    file is in that seat's directory. Closing the room stops every one."""
    resource = str(uuid.uuid4())
    home = tmp_path / resource
    directory = home / ".cheese"
    directory.mkdir(parents=True)
    helper = directory / "cheese-tunnel.py"
    helper.write_text(
        "import sys, time\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).touch()\ntime.sleep(60)\n"
    )
    processes = []
    try:
        for seat in ("aaaa", "bbbb"):
            seat_dir = directory / "seats" / seat
            seat_dir.mkdir(parents=True)
            ready = seat_dir / "ready"
            process = subprocess.Popen([sys.executable, str(helper), str(ready)])
            processes.append(process)
            wait_for(ready.exists)
            (seat_dir / "cheese-tunnel.pid").write_text(str(process.pid))

        cleanup.stop_executor(home, resource)

        for process in processes:
            assert process.wait(timeout=5) != 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


def test_teardown_stops_the_executor_a_previous_root_installed(tmp_path):
    """Closing a room reads what preparing it wrote.

    A room prepared before the platform moved its own files still has its
    executor, and every marker describing it, where that launcher put them —
    nothing moves them until the room is prepared again, and a room being torn
    down never will be. Reading only the current root answers "this room never
    had an executor", and that answer is acted on: the detached daemon is left
    running under a home that is then deleted out from under it, and the private
    seat it holds is never released.
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


def test_teardown_reads_a_target_a_seat_kept_in_its_own_directory(tmp_path):
    """A seat's target is written into the seat (`place.seat_dir`), so the
    teardown has to read there too.

    Reading only the room's own directory answers "this room never had an
    executor" for a room whose every session installed one, and that answer is
    acted on: the detached daemon is left running under a home that is then
    deleted out from under it, and the private container it holds is never
    released — the leak the room-level read exists to prevent.
    """
    resource = str(uuid.uuid4())
    home = tmp_path / resource
    seat = home / ".cheese/seats/4b9f7d802648"
    (seat / "remote-execution").mkdir(parents=True)
    (seat / "remote-target.json").write_text(
        json.dumps({"kind": "device", "resource_id": resource})
    )

    target = cleanup.session_target(home, resource)
    assert target is not None and target["kind"] == "device"
    with pytest.raises(RuntimeError, match="another resource generation"):
        cleanup.session_target(home, str(uuid.uuid4()))


def test_a_private_chats_container_is_released_out_of_the_seat_that_holds_it(
    tmp_path,
):
    """The helper is read out of the installation the target came from.

    A seat keeps its own copy of the release (the launcher writes it into the
    seat that owns the session) and the room level keeps none, so reading the
    room level raised FileNotFoundError, the release never ran, and the private
    container stayed on the machine holding its scratch for good — the leak
    `target_markers` reads seats to prevent.
    """
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home, _work = cleanup.resource_paths(tmp_path, project, resource)
    seat = home / ".cheese/seats/4b9f7d802648"
    (seat / "remote-execution").mkdir(parents=True)
    (seat / "remote-target.json").write_text(
        json.dumps({"kind": "private", "topic": resource})
    )
    asked = tmp_path / "asked-to-release.json"
    (seat / "remote-execution/private.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "def release(config):\n"
        f"    Path({str(asked)!r}).write_text(json.dumps(config))\n"
    )

    removed = run_cleanup(tmp_path, "remove", project, resource)

    assert removed.returncode == 0, removed.stderr
    assert json.loads(asked.read_text()) == {"kind": "private", "topic": resource}
    assert not home.exists()


def test_private_cleanup_rejects_another_generation(tmp_path):
    resource = str(uuid.uuid4())
    marker = tmp_path / ".cheese/remote-target.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"kind": "private", "topic": resource}))
    assert cleanup.session_target(tmp_path, resource)["topic"] == resource
    with pytest.raises(RuntimeError, match="another resource generation"):
        cleanup.session_target(tmp_path, str(uuid.uuid4()))


def test_removal_deletes_a_home_whatever_was_not_pushed(tmp_path):
    """A room's cleanup gives its sessions one checkpoint before it gets here,
    and every turn left a snapshot: nothing on the machine holds the home."""
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home, _work = cleanup.resource_paths(tmp_path, project, resource)
    task = home / ".cheese/tasks" / str(uuid.uuid4())
    task.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(task)], check=True)
    (task / "source.py").write_text("work in progress")
    subprocess.run(["git", "init", "-q", str(home / "room")], check=True)
    (home / "room/notes.md").write_text("unfinished research")

    removed = run_cleanup(tmp_path, "remove", project, resource)

    assert removed.returncode == 0, removed.stderr
    assert not home.exists()


class _Bucket:
    """A presigned PUT URL's other end: what it was sent, and its answer."""

    def __init__(self, status=200):
        received = self.received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                length = int(self.headers["Content-Length"])
                received.append(self.rfile.read(length))
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/kept.tar.gz?sig=x"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def bucket():
    answering = _Bucket()
    yield answering
    answering.close()


def keep_room(machine_home, project, resource, url):
    return subprocess.run(
        [sys.executable, cleanup.__file__, "keep-room", project, resource, "-", "-"],
        # The bucket here is on loopback, past any proxy the environment names.
        env={
            **os.environ,
            "HOME": str(machine_home),
            "CHEESE_KEEP_URL": url,
            "no_proxy": "*",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


def _old_room(tmp_path):
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home, _work = cleanup.resource_paths(tmp_path, project, resource)
    (home / "room/memory").mkdir(parents=True)
    (home / "room/memory/notes.md").write_text("365 notes")
    (home / "room/deck.pptx").write_bytes(b"PK slides")
    return project, resource, home


def test_an_old_rooms_files_are_sent_to_the_bucket_as_they_are(tmp_path, bucket):
    project, resource, home = _old_room(tmp_path)

    kept = keep_room(tmp_path, project, resource, bucket.url)

    assert kept.returncode == 0, kept.stderr
    [sent] = bucket.received
    answer = json.loads(kept.stdout)["kept"]
    assert answer == {"size": len(sent), "md5": hashlib.md5(sent).hexdigest()}
    with tarfile.open(fileobj=io.BytesIO(sent)) as bundle:
        files = {
            member.name: bundle.extractfile(member).read()
            for member in bundle.getmembers()
            if member.isfile()
        }
    assert files == {
        "room/memory/notes.md": b"365 notes",
        "room/deck.pptx": b"PK slides",
    }
    # Sending is not deleting: the room's cleanup does that afterwards.
    assert (home / "room/deck.pptx").exists()
    assert not list((tmp_path / ".cheese/cleanup").glob("*.tar.gz"))


@pytest.mark.parametrize("room", ["git checkout", "empty", "has an executor", "none"])
def test_a_home_with_nothing_off_the_forge_sends_nothing(tmp_path, bucket, room):
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home, _work = cleanup.resource_paths(tmp_path, project, resource)
    home.mkdir(parents=True)
    if room != "none":
        (home / "room").mkdir()
    if room == "git checkout":
        subprocess.run(["git", "init", "-q", str(home / "room")], check=True)
        (home / "room/source.py").write_text("committed or not, it has a forge")
    if room == "has an executor":
        (home / "room/out.txt").write_text("made in a sandbox")
        (home / ".cheese").mkdir()
        (home / ".cheese/remote-target.json").write_text(
            json.dumps({"kind": "device", "resource_id": resource})
        )

    kept = keep_room(tmp_path, project, resource, bucket.url)

    assert kept.returncode == 0, kept.stderr
    assert json.loads(kept.stdout) == {"kept": None}
    assert bucket.received == []


def test_an_old_rooms_files_are_not_dropped_when_there_is_no_bucket(tmp_path):
    project, resource, _home = _old_room(tmp_path)

    kept = keep_room(tmp_path, project, resource, "")

    assert kept.returncode != 0
    assert "no bucket" in kept.stderr


def test_a_refused_upload_is_a_failure(tmp_path):
    project, resource, _home = _old_room(tmp_path)
    refusing = _Bucket(status=403)
    try:
        kept = keep_room(tmp_path, project, resource, refusing.url)
    finally:
        refusing.close()

    assert kept.returncode != 0
