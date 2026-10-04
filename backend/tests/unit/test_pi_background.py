"""A pi room's background jobs: they run on the room's machine, outlive the turn,
and what they print reaches the session host where the job tools read it.

Driven through the real runner against a real executor standing for the room's
machine (`tests/support/room_machine.py`). What is asserted is what a person
relying on a background job can observe: it keeps going after the call that
started it returned, it has a terminal to be typed into, stopping it stops what
it started, and it goes when the room does.
"""

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.jobs import Jobs
from app.domain.agent.harness.pi.machine import Machine
from app.domain.agent.harness.pi.runner import Runner
from tests.support.room_machine import room_machine
from tests.unit.test_pi_runner import call, shim

pytestmark = pytest.mark.anyio


async def until(check, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.1)


def output(runner: Runner, job: str) -> str:
    path = runner.state / "bg" / job / "output"
    return path.read_text(errors="replace") if path.exists() else ""


def ended(runner: Runner, job: str) -> dict | None:
    path = runner.state / "bg" / job / "exit"
    return json.loads(path.read_text()) if path.exists() else None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def started(tmp_path: Path, target: dict, **options) -> Runner:
    runner = Runner(tmp_path / "state", **options)
    await runner.start(
        SessionStart("system prompt", None, agent_handle="teammate"),
        binary=shim(tmp_path),
        cwd=str(tmp_path),
        env={"PATH": os.environ["PATH"]},
        args=[],
        target=target,
    )
    return runner


async def test_starting_a_job_returns_at_once_and_the_job_keeps_going(tmp_path):
    with room_machine(tmp_path / "machine") as target:
        runner = await started(tmp_path, target)
        try:
            began = time.monotonic()
            job = (
                await call(
                    runner.state,
                    "job_start",
                    {"command": "echo first; sleep 2; echo later > made.txt"},
                )
            )["id"]
            assert time.monotonic() - began < 2
            assert ended(runner, job) is None
            # A session with a job running is busy: a changed launch waits for
            # it rather than ending the job with the runner (`host.configure`).
            assert (await call(runner.state, "ping"))["tasks"] == 1
            await until(lambda: ended(runner, job) is not None)
            assert ended(runner, job)["status"] == 0
            assert (await call(runner.state, "ping"))["tasks"] == 0
            assert "first" in output(runner, job)
            # It ran in the checkout on the machine.
            assert (Path(target["workspace"]) / "made.txt").read_text() == "later\n"
            assert not (tmp_path / "made.txt").exists()
        finally:
            await runner.close()


async def test_the_command_is_given_a_terminal_and_can_be_typed_into(tmp_path):
    with room_machine(tmp_path / "machine") as target:
        runner = await started(tmp_path, target)
        try:
            job = (
                await call(
                    runner.state,
                    "job_start",
                    {
                        "command": "[ -t 0 ] && echo ON-A-TERMINAL; "
                        "read line; echo got:$line"
                    },
                )
            )["id"]
            await until(lambda: "ON-A-TERMINAL" in output(runner, job))
            await call(runner.state, "job_write", {"id": job, "text": "hello\n"})
            await until(lambda: "got:hello" in output(runner, job))
            await until(lambda: ended(runner, job) is not None)
            with pytest.raises(RuntimeError, match="已结束"):
                await call(runner.state, "job_write", {"id": job, "text": "more\n"})
        finally:
            await runner.close()


async def test_stopping_a_job_stops_what_it_started(tmp_path):
    with room_machine(tmp_path / "machine") as target:
        runner = await started(tmp_path, target)
        pids = Path(target["workspace"]) / "pid"
        try:
            job = (
                await call(
                    runner.state,
                    "job_start",
                    {"command": f"sleep 300 & echo $! > {pids}; wait"},
                )
            )["id"]
            await until(lambda: pids.exists() and pids.read_text().strip())
            child = int(pids.read_text())
            await call(runner.state, "job_signal", {"id": job, "signal": 15})
            await until(lambda: ended(runner, job) is not None)
            await until(lambda: not alive(child))
        finally:
            await runner.close()


async def test_closing_the_room_takes_its_background_jobs_with_it(tmp_path):
    with room_machine(tmp_path / "machine") as target:
        runner = await started(tmp_path, target)
        pids = Path(target["workspace"]) / "pid"
        await call(
            runner.state,
            "job_start",
            {"command": f"sleep 300 & echo $! > {pids}; wait"},
        )
        await until(lambda: pids.exists() and pids.read_text().strip())
        child = int(pids.read_text())
        await runner.close()
        await until(lambda: not alive(child))


async def test_a_session_with_a_job_running_on_the_machine_is_not_let_go(tmp_path):
    """An idle session is let go (`driven/runner.py`), but one whose job still
    runs on the machine is not idle: letting it go would leave the job with
    nobody reading it, and the executor stops what nobody reads."""
    with room_machine(tmp_path / "machine") as target:
        runner = await started(tmp_path, target, idle_exit_s=0.5)
        try:
            job = (await call(runner.state, "job_start", {"command": "sleep 3"}))["id"]
            assert runner.process is not None
            await asyncio.sleep(2)
            assert runner.process.returncode is None, "let go with a job running"
            await until(lambda: ended(runner, job) is not None)
            await asyncio.wait_for(runner.process.wait(), 20)
        finally:
            await runner.close()


async def test_a_runner_that_comes_back_picks_a_job_up_where_it_was(tmp_path):
    """A job outlives the runner that started it: the next one copies on from
    the last byte the first one wrote down, and records how it ended."""
    with room_machine(tmp_path / "machine") as target:
        directory = tmp_path / "bg"
        first = Jobs(Machine(target), directory)
        job = (
            await first.start(
                "echo one; sleep 2; echo two", label="", cwd=target["workspace"]
            )
        )["id"]
        await until(lambda: "one" in (directory / job / "output").read_text())
        await first.close(end=False)

        second = Jobs(Machine(target), directory)
        second.resume()
        await until(lambda: (directory / job / "exit").exists())
        said = (directory / job / "output").read_text()
        assert said.count("one") == 1 and "two" in said
        await second.close(end=False)
