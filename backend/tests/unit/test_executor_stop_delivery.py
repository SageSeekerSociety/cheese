"""Stopping a command on a machine: the executor takes the host's stop on
every platform it runs on, and the host does not hammer an executor that keeps
refusing one.

On dev on 2026-10-04 an executor on a Windows machine refused every stop, and
the host retried each one about once a second for ten minutes: about 1,900
failing calls an hour from two rooms, every one of them through the backend.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.pinned_claude import claude_binary

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "app/domain/agent/harness/claude_code/remote_execution"
)


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"stop_delivery_{name}", SOURCE / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SOURCE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SOURCE))
    return module


# Every process of the executor gets a `signal` module with what Windows has:
# no SIGHUP and no SIGKILL. The executor forks and re-execs itself, so this
# has to be in each interpreter it starts, which is what sitecustomize is.
# Everything else about it is the real runtime.
WINDOWS_SIGNALS = "import signal\ndel signal.SIGHUP, signal.SIGKILL\n"


@pytest.fixture
def windows_executor(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state = tmp_path / "state"
    windows = tmp_path / "windows"
    windows.mkdir()
    (windows / "sitecustomize.py").write_text(WINDOWS_SIGNALS)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(windows),
    }
    runtime_path = str(SOURCE / "runtime.py")
    started = subprocess.run(
        [sys.executable, runtime_path, "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(workspace.resolve()),
                "claude": claude_binary(),
                "env": {},
            }
        ),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert started.returncode == 0, started.stderr
    runtime = _load("runtime")

    def control(**params):
        return runtime.request(state, "control", {"subtype": "shell", **params})

    yield control
    subprocess.run(
        [sys.executable, runtime_path, "stop", "--state", str(state)],
        capture_output=True,
        timeout=30,
    )


# The numbers a session's host sends: SIGTERM, SIGINT, SIGHUP, SIGKILL.
@pytest.mark.parametrize("number", [15, 2, 1, 9])
def test_an_executor_without_sighup_or_sigkill_still_takes_the_hosts_stop(
    windows_executor, number
):
    # A stop that overtakes its start is kept, and the start is refused: the
    # stop arrived, on whatever platform the command would have run.
    windows_executor(operation="signal", command_id=f"stop-{number}", signal=number)
    started = windows_executor(
        operation="start",
        command_id=f"stop-{number}",
        kind="sh",
        body="true",
        cwd="/",
        env={},
        merge=True,
        stdin=None,
    )
    assert started == {"started": False}


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _deliver(monkeypatch, answer, written):
    """Run the stop watcher against an executor that answers with `answer`,
    after its prefix wrote `written` and went away. Returns each attempt as
    (time, signal)."""
    client = _load("client")
    clock = _Clock()
    attempts = []

    class Executor:
        def __init__(self, _target):
            pass

        def call(self, method, params=None, **_options):
            attempts.append((clock.now, params["signal"]))
            return answer(clock.now, client)

    monkeypatch.setattr(client, "time", clock)
    monkeypatch.setattr(client, "RemoteClient", Executor)
    monkeypatch.setattr(client, "_current_target", lambda target: target)
    read_end, write_end = os.pipe()
    os.write(write_end, written)
    os.close(write_end)
    try:
        client._deliver_stop({"kind": "device"}, "shell-stopped", read_end)
    finally:
        os.close(read_end)
    return attempts


def test_a_stop_the_executor_keeps_refusing_is_retried_with_backoff(monkeypatch):
    def refuse(_now, client):
        raise client.MachineOutOfReach()

    # The prefix was killed: its pipe closes with no word, which is a TERM.
    attempts = _deliver(monkeypatch, refuse, b"")
    term = [at for at, number in attempts if number == 15]
    kill = [at for at, number in attempts if number == 9]
    # Still tried for the whole window, so a link that comes back gets the stop.
    assert term[-1] > 500
    assert kill[-1] - kill[0] > 500
    # But not once a second, which was about 1,200 calls for one stop.
    assert len(term) < 40
    assert len(kill) < 40


def test_a_stop_is_delivered_soon_after_the_executor_answers_again(monkeypatch):
    def come_back(now, client):
        if now < 20:
            raise client.MachineOutOfReach()
        return {"running": False}

    attempts = _deliver(monkeypatch, come_back, b"stop 15\n")
    term = [at for at, number in attempts if number == 15]
    # Tried until it got through, then no more; delivered within one backoff
    # step of the executor coming back.
    assert term[-1] >= 20
    assert term[-1] < 20 + 30
    assert len(term) == len([at for at in term if at < 20]) + 1
