"""A session started again with new settings takes the message sent next.

Starting a task relaunches its idle session with a credential that may write.
The runner it had goes, and the reading that was following it hears so. On dev
(2026-10-06) that reading heard it while the new runner was being started, and
the task's first instruction then failed with "The session was not started by
this process".

Both orders are here: the old runner's exit heard during the relaunch, and
heard only after it.
"""

import asyncio
import uuid
from pathlib import Path
from typing import cast

import pytest

from app.domain.agent.harness import PI
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.reads import Ended
from app.domain.agent.session_host.contract import (
    Access,
    Owner,
    Prompt,
    SessionRef,
    SessionSpec,
)
from app.domain.agent.session_host.driver import Driver as HarnessDriver
from app.domain.agent.session_host.driver import Launched
from app.domain.agent.session_host.host import SessionHost

HOST = "machine"


class Machine:
    """The session host: one runner at a time behind the session's socket,
    and what was said to it."""

    def __init__(self):
        self.answers: list[bool] = []
        self.alive = True
        self.changed = asyncio.Event()
        self.said: list[str] = []
        self.launches = 0

    def is_online(self, device_id: str) -> bool:
        return device_id == HOST

    async def call_executor(self, device_id, state, method, params, **_):
        if method in ("send", "steer"):
            self.said.append(params["text"])
        return {}

    def answer(self, alive: bool) -> None:
        """The next read of the session's socket answers ``alive``."""
        self.answers.append(alive)
        self.changed.set()

    async def read(self, wait: float) -> bool:
        if not self.answers and wait:
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), wait)
            except TimeoutError:
                pass
        return self.answers.pop(0) if self.answers else self.alive


class Following:
    def __init__(self, machine: Machine):
        self.machine = machine
        self.heard: dict = {}

    async def drain(self, wait: float) -> None:
        self.heard = {"alive": await self.machine.read(wait)}

    async def reconcile_history(self) -> None:
        pass

    def unpark(self) -> None:
        self.machine.changed.set()

    async def release(self) -> None:
        pass


class Driver:
    harness = PI
    label = "pi"
    read_failure = "reading failed"
    steer = "steer"
    stop = ("abort", "aborted")
    receipt_on_accept = True
    mirror = "journal"

    def __init__(self, machine: Machine, relaunch):
        self.machine = machine
        self.relaunch = relaunch

    def working(self, status):
        return False

    def conversation(self, status):
        return "conversation"

    def takes_inputs(self, status):
        return True

    async def launch(self, wire, host, ref, spec, access, known):
        if self.machine.launches:
            await self.relaunch()
        self.machine.launches += 1
        return Launched(
            "conversation", frozenset({LONG_POLL}), f"launch-{self.machine.launches}"
        )

    def subscription(self, seat, acting, mirror, call, launched, readers):
        return Following(self.machine)

    def images(self, images):
        return []

    async def adopt(self, wire, found):
        pass


def _access() -> Access:
    owner = Owner(uuid.uuid4(), uuid.uuid4(), "resource", "task", "cheese")
    return Access("token", host=HOST, owner=owner)


def _spec(keeps_nothing: bool) -> SessionSpec:
    return SessionSpec(
        system_prompt="",
        model="m",
        env={"CHEESE_KEEPS_NOTHING": "1" if keeps_nothing else "0"},
    )


async def _started_task(tmp_path: Path, relaunch):
    machine = Machine()
    host = SessionHost(machine, mirrors=tmp_path)
    host._drivers = {PI: cast(HarnessDriver, Driver(machine, relaunch))}
    ref = SessionRef(PI, "task-session")
    access = _access()
    await host.start(ref, _spec(keeps_nothing=True), access)
    heard: list = []

    async def follow():
        async for read in host.read(ref):
            heard.append(read.event)

    reading = asyncio.create_task(follow())
    return machine, host, ref, access, heard, reading


async def _until(condition) -> None:
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_an_old_runner_going_during_the_relaunch_leaves_the_new_one(
    tmp_path,
):
    heard: list = []
    machine: Machine

    async def old_runner_goes_while_the_new_one_starts():
        machine.answer(False)
        await _until(lambda: any(isinstance(e, Ended) for e in heard))

    machine, host, ref, access, heard, reading = await _started_task(
        tmp_path, old_runner_goes_while_the_new_one_starts
    )

    await host.start(ref, _spec(keeps_nothing=False), access)
    await host.send(ref, Prompt(uuid.uuid4(), "开始"), work_id=uuid.uuid4())

    assert machine.said == ["开始"]
    reading.cancel()
    await asyncio.gather(reading, return_exceptions=True)


@pytest.mark.anyio
async def test_an_old_runner_heard_going_after_the_relaunch_leaves_the_new_one(
    tmp_path,
):
    async def relaunch():
        pass

    machine, host, ref, access, heard, reading = await _started_task(tmp_path, relaunch)
    await _until(lambda: heard)

    await host.start(ref, _spec(keeps_nothing=False), access)
    # The answer the old runner gave on its way out arrives only now.
    machine.answer(False)
    await asyncio.sleep(0.05)
    await host.send(ref, Prompt(uuid.uuid4(), "开始"), work_id=uuid.uuid4())

    assert machine.said == ["开始"]
    assert host.answers(ref) is True
    assert not any(isinstance(e, Ended) for e in heard)
    reading.cancel()
    await asyncio.gather(reading, return_exceptions=True)
