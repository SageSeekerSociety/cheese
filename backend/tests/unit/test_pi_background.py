"""后台命令：起来、活着、能打字进去、被停掉，以及房间关掉时被收走。

Driven against the real supervisor as a real process, because everything worth
testing here is about processes: that the job is in its own session, that a pty
is what the command sees, and that killing it kills what it started.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.domain.agent.harness import Opening
from app.domain.agent.harness.pi import background
from app.domain.agent.harness.pi.runner import Runner
from tests.unit.test_pi_runner import EXTENSION, shim

SUPERVISOR = Path(background.__file__)


def start(directory: Path, command: str, *, cwd: str = "/tmp", label: str = "") -> Path:
    subprocess.run(
        [
            sys.executable,
            str(SUPERVISOR),
            "--dir",
            str(directory),
            "--cwd",
            cwd,
            "--command",
            command,
            "--label",
            label,
        ],
        check=True,
        timeout=30,
    )
    return directory


def until(check, *, seconds: float = 15.0):
    """Wait for something a separate process does on its own schedule."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            found = check()
        except OSError:
            found = None
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError("never happened")


def output(directory: Path) -> str:
    try:
        return (directory / "output").read_bytes().decode("utf-8", "replace")
    except FileNotFoundError:
        return ""


def tell(directory: Path, request: dict) -> dict:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(10)
    connection.connect(str(directory / "sock"))
    try:
        connection.sendall(json.dumps(request).encode() + b"\n")
        return json.loads(connection.recv(65536))
    finally:
        connection.close()


def test_starting_a_job_returns_at_once_and_the_job_keeps_going(tmp_path):
    """The whole reason this exists: a turn must not block on a long command."""
    began = time.monotonic()
    job = start(tmp_path / "j", "sleep 0.4; echo 一; sleep 0.4; echo 二")
    assert time.monotonic() - began < 5, "starting a job waited for it to finish"

    until(lambda: "一" in output(job))
    assert not (job / "exit").exists(), "still running"
    until(lambda: (job / "exit").exists())
    assert json.loads((job / "exit").read_text())["status"] == 0
    assert output(job).replace("\r\n", "\n").split() == ["一", "二"]


def test_the_command_is_given_a_terminal_and_can_be_typed_into(tmp_path):
    """A pipe would buy neither half of this.

    Programs ask `isatty` and buffer on the answer, so over a pipe a prompt can
    sit unflushed indefinitely; and a REPL only offers one to a terminal. The
    pty is what makes writing to a job mean something other than starting it
    again.
    """
    job = start(tmp_path / "repl", f"{sys.executable} -u -i")
    until(lambda: ">>>" in output(job))
    assert tell(job, {"write": "print(6 * 7)\n"})["ok"]
    until(lambda: "42" in output(job))
    assert not (job / "exit").exists(), "a REPL stays open between writes"
    tell(job, {"signal": int(signal.SIGTERM)})
    until(lambda: (job / "exit").exists())


def test_a_command_that_cannot_run_is_recorded_rather_than_lost(tmp_path):
    job = start(tmp_path / "bad", "definitely-not-a-command")
    until(lambda: (job / "exit").exists())
    assert json.loads((job / "exit").read_text())["status"] != 0
    assert "not" in output(job).lower(), "the shell's own complaint is kept"


def test_stopping_a_job_stops_what_it_started(tmp_path):
    """Signalling the shell alone leaves the real process running, holding
    whatever port or file it had — and nothing left to name it by."""
    marker = tmp_path / "still-here"
    job = start(
        tmp_path / "tree",
        f"{sys.executable} -u -c "
        f'"import time,pathlib\nwhile True:\n'
        f" pathlib.Path('{marker}').touch(); time.sleep(0.1)\" & wait",
    )
    until(lambda: marker.exists())
    inner = json.loads((job / "meta.json").read_text())["child"]
    tell(job, {"signal": int(signal.SIGTERM)})
    until(lambda: (job / "exit").exists())

    marker.unlink()
    time.sleep(0.6)
    assert not marker.exists(), "the grandchild outlived the job it belonged to"
    with pytest.raises(ProcessLookupError):
        os.killpg(os.getpgid(inner), 0)


def test_a_job_is_not_in_the_process_group_that_starting_it_belonged_to(tmp_path):
    """Its own session, which is what lets it outlive pi — and what stops a
    signal aimed at the session from reaching it by accident."""
    job = start(tmp_path / "own", "sleep 5")
    meta = json.loads(until(lambda: (job / "meta.json").read_text()))
    assert os.getpgid(meta["pid"]) != os.getpgid(os.getpid())
    tell(job, {"signal": int(signal.SIGKILL)})


@pytest.mark.anyio
async def test_closing_the_room_takes_its_background_jobs_with_it(tmp_path):
    """Not killed at the end of a turn — that is the point of them. But this
    runner IS the screen's program, so when it goes the room is being torn
    down, and a dev server nobody can reach any more would hold its port until
    somebody found it by hand.
    """
    runner = Runner(tmp_path / "state")
    await runner.start(
        Opening("system prompt", None, agent_handle="teammate"),
        binary=shim(tmp_path),
        cwd=str(tmp_path),
        env={"PATH": os.environ["PATH"]},
        args=["--no-context-files"],
        extension=EXTENSION,
    )
    job = start(runner.state / "bg" / "job-1", "sleep 30")
    held = json.loads((job / "meta.json").read_text())["child"]
    await runner.close()

    until(lambda: (job / "exit").exists())
    with pytest.raises(ProcessLookupError):
        os.killpg(os.getpgid(held), 0)
