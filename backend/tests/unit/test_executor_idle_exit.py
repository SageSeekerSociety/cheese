"""An executor nothing uses stops, and takes what its commands left with it.

Exercised through the executor's own process and socket, the way a machine runs
it: `runtime.py start`, requests on its socket, `runtime.py stop`. The idle
time is shortened through the variable `serve` reads for that.
"""

import base64
import contextlib
import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

RUNTIME = (
    Path(__file__).resolve().parents[2]
    / "app/domain/agent/harness/claude_code/remote_execution/runtime.py"
)
spec = importlib.util.spec_from_file_location("idle_exit_runtime", RUNTIME)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

# Kept short: every test here waits it out at least once, under the pure
# layer's per-test ceiling.
IDLE_S = 1


def _start(state, workspace, *, env=None, private=False):
    # A home of the test's own: the machine's could hold a live preview.
    home = state.parent / "home"
    (home / ".cheese").mkdir(parents=True, exist_ok=True)
    config = {"workspace": str(workspace), "env": {"HOME": str(home), **(env or {})}}
    if private:
        config["private"] = True
    result = subprocess.run(
        [sys.executable, str(RUNTIME), "start", "--state", str(state)],
        input=json.dumps(config),
        env={**os.environ, "CHEESE_EXECUTOR_IDLE_EXIT_S": str(IDLE_S)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["pid"]


def _stop(state):
    return subprocess.run(
        [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _stopped(state):
    """Nothing serves the state: no socket, and its lock is free."""
    if Path(runtime.socket_path(state)).exists():
        return False
    with (state / "service.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(lock, fcntl.LOCK_UN)
    return True


def _await_stopped(state, within):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if _stopped(state):
            return True
        time.sleep(0.1)
    return False


def _alive(pid):
    """Running, as opposed to gone or a zombie nobody has reaped yet."""
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    )
    stat = result.stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def _shell(state, workspace, command_id, body):
    runtime.request(
        state,
        "control",
        {
            "subtype": "shell",
            "operation": "start",
            "command_id": command_id,
            "kind": "sh",
            "body": body,
            "cwd": str(workspace),
            "env": {},
            "merge": True,
            "stdin": None,
        },
    )


def _finish(state, command_id, timeout=30):
    output, deadline = b"", time.monotonic() + timeout
    while True:
        answer = runtime.request(
            state,
            "control",
            {
                "subtype": "shell",
                "operation": "read",
                "command_id": command_id,
                "out": len(output),
                "err": 0,
                "wait": 1,
            },
        )
        output += base64.b64decode(answer["out"])
        if "exit" in answer:
            return output.decode()
        assert time.monotonic() < deadline, output


def _end(pid):
    """A detached test process a failed run would otherwise leave behind."""
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def _events(state, item, status):
    """When the executor logged `item` reaching `status`, from its event log."""
    found = []
    for line in (state / "events.jsonl").read_text().splitlines():
        event = json.loads(line)
        if event["item"] == item and event["status"] == status:
            found.append(datetime.fromisoformat(event["time"]))
    return found


def _detach(state, workspace, name):
    """Start what an agent starts and walks away from — a dev server put in
    the background with `nohup … &` — and answer its pid."""
    _shell(
        state,
        workspace,
        name,
        f"nohup sleep 300 >/dev/null 2>&1 & echo $! > {name}.pid",
    )
    _finish(state, name)
    pid = int((workspace / f"{name}.pid").read_text())
    assert _alive(pid)
    return pid


def test_an_idle_executor_ends_what_its_commands_left_and_comes_back_when_asked(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    first = _start(state, workspace)
    left = None
    try:
        left = _detach(state, workspace, "left")

        assert _await_stopped(state, IDLE_S + 15), "the idle executor kept running"
        assert not _alive(left), "what the command left running outlived it"
        # Room cleanup's stop finds nothing to stop, and says so without failing.
        assert _stop(state).returncode == 0
        # The next tool call's install starts it again on the same state.
        second = _start(state, workspace)
        assert second != first
        assert runtime.request(state, "ping")["pid"] == second
    finally:
        _stop(state)
        if left is not None:
            _end(left)


def test_a_running_command_keeps_the_executor_up_until_idle_after_it_ends(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    _start(state, workspace)
    try:
        _shell(state, workspace, "background", "sleep 2.5; touch finished")

        time.sleep(IDLE_S * 2)
        assert not _stopped(state)
        assert not (workspace / "finished").exists()

        assert _await_stopped(state, 2.5 + IDLE_S + 15)
        assert (workspace / "finished").exists(), "the running command was ended"
        # Idle is counted from when the command ended, not from the last request.
        (exited,) = _events(state, "background", "exited")
        (stopped,) = _events(state, "service", "idle")
        assert (stopped - exited).total_seconds() >= IDLE_S
    finally:
        _stop(state)


def test_a_declared_preview_keeps_the_executor_and_its_server_up(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    (home / ".cheese").mkdir(parents=True)
    state = tmp_path / "state"
    # What `cheese serve` leaves: the declared port and the helper's pid.
    (home / ".cheese/cheese-preview.port").write_text("5173\n")
    helper = subprocess.Popen(["sleep", "300"])
    (home / ".cheese/cheese-preview.pid").write_text(f"{helper.pid}\n")
    _start(state, workspace, env={"HOME": str(home)})
    server = None
    try:
        server = _detach(state, workspace, "server")

        time.sleep(IDLE_S * 2.5)
        assert not _stopped(state)
        assert _alive(server)

        helper.kill()
        helper.wait()
        assert _await_stopped(state, IDLE_S + 15)
        assert not _alive(server)
    finally:
        helper.kill()
        helper.wait()
        _stop(state)
        if server is not None:
            _end(server)


def test_a_private_chats_executor_is_not_stopped_for_sitting_idle(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    pid = _start(state, workspace, private=True)
    try:
        time.sleep(IDLE_S * 2.5)
        assert runtime.request(state, "ping")["pid"] == pid
    finally:
        _stop(state)
