"""A tunnel helper on the central session host ends once its session has.

The launch that starts a helper stops it when it ends, but a helper that launch
lost ran on: on dev's session host on 2026-10-13, ten helpers were still
running whose sessions had ended a week before. These run real processes and
read the real /proc, so they skip where there is none (macOS). The scan is
narrowed to the processes each test started, so a test never touches a helper
of a session running on the machine it runs on.
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.domain.agent import machine_tunnel

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/environ").exists(), reason="reads /proc"
)


def _config(tmp_path, seat: str) -> str:
    directory = tmp_path / seat / "remote-session"
    directory.mkdir(parents=True)
    return str(directory / "execution.json")


def _start(argv, config: str | None) -> subprocess.Popen:
    env = {
        key: value
        for key, value in os.environ.items()
        if key != machine_tunnel.EXECUTION_CONFIG
    }
    if config is not None:
        env[machine_tunnel.EXECUTION_CONFIG] = config
    return subprocess.Popen(argv, env=env)


def _session_process(config: str) -> subprocess.Popen:
    """A process of the session: what the runner, `claude` and the commands it
    starts all are, as far as the variable goes."""
    return _start(["sleep", "60"], config)


def _old_helper(tmp_path, config: str | None) -> subprocess.Popen:
    """A helper from before helpers watched their sessions: a process running a
    file of the helper's shipped name, in its session's environment, that
    nothing will stop."""
    script = tmp_path / "old" / "cheese-tunnel.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text("import time\ntime.sleep(60)\n")
    return _start([sys.executable, str(script)], config)


def _view_server(tmp_path, config: str) -> subprocess.Popen:
    """The forwarded project view's server, which carries the session's
    variable and waits for the session to end, as the helper does."""
    script = tmp_path / "view" / "forwarded_fs.py"
    script.parent.mkdir(exist_ok=True)
    script.write_text("import time\ntime.sleep(60)\n")
    return _start([sys.executable, str(script)], config)


def _only(processes):
    pids = {process.pid for process in processes}
    return lambda: (seen for seen in machine_tunnel._processes() if seen[0] in pids)


def _stopped(process: subprocess.Popen, timeout: float = 5) -> bool:
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


@pytest.fixture
def started():
    processes: list[subprocess.Popen] = []
    yield processes
    for process in processes:
        process.kill()
        process.wait(timeout=5)


def test_a_helper_ends_once_its_session_has_and_not_before(tmp_path, started):
    config = _config(tmp_path, "seat")
    session = _session_process(config)
    # Waiting for the session, as this helper does: it keeps nothing alive.
    view = _view_server(tmp_path, config)
    started += [session, view]
    ended = threading.Event()
    threading.Thread(
        target=machine_tunnel.watch_session,
        args=(config,),
        kwargs={"every_s": 0.05, "end": ended.set, "processes": _only(started)},
        daemon=True,
    ).start()

    assert not ended.wait(0.5), "the helper ended while its session was running"
    session.kill()
    session.wait(timeout=5)
    assert ended.wait(5), "the helper outlived its session"


def test_one_sweep_stops_every_helper_whose_session_ended_and_no_other(
    tmp_path, started
):
    running = _config(tmp_path, "running")
    ended = _config(tmp_path, "ended")
    session = _session_process(running)
    live_helper = _old_helper(tmp_path, running)
    abandoned = _old_helper(tmp_path, ended)
    # One whose session it cannot name: nothing tells whether that has ended.
    unnamed = _old_helper(tmp_path, None)
    started += [session, live_helper, abandoned, unnamed]
    time.sleep(0.2)  # every one of them is in /proc with its environment

    # A helper this young may belong to a launch whose agent has not started.
    assert machine_tunnel.stop_abandoned_helpers(_only(started)(), grace_s=3600) == 0
    assert machine_tunnel.stop_abandoned_helpers(_only(started)(), grace_s=0) == 1

    assert _stopped(abandoned), "a helper whose session ended was left running"
    for process in (session, live_helper, unnamed):
        assert process.poll() is None
