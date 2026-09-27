"""A room that starts Claude Code learns at once when the session has died.

The session host here is this machine: a HOME on disk, the runner archive
started the way the launcher starts it (its stderr appended to ``runner.log``),
and a connector that relays one JSON line to the runner's socket and fails the
way the real one does when nothing is listening. What is checked is how long a
room waits and what it is told, through the channel's own ``ensure``.
"""

import asyncio
import fcntl
import json
import os
import shlex
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.agent.harness import CLAUDE_CODE, Opening, SessionRef
from app.domain.agent.harness.channel import Placement, ScreenSetupError
from app.domain.agent.harness.claude_code import ClaudeCodeChannel
from app.domain.agent.harness.claude_code import channel as claude_channel
from app.domain.agent.harness.claude_code.bundle import build
from app.domain.agent.harness.driven.runner import socket_path

PROJECT, TOPIC = uuid.uuid4(), uuid.uuid4()
AGENT = "cheese-agent"
DEVICE = "dev1"


class Host:
    """The central channel's surface the Claude Code channel uses, on this disk."""

    name = "central"
    provisions_machine = False
    deferred_work = False
    _session_factory = None
    # What the room was told when the session did not come up.
    refusal = ""

    def __init__(self, root: Path, command: str | None, *, before=None):
        self.home = root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.artifact = root / "runner.pyz"
        self.artifact.write_bytes(build())
        # None: the launcher is still preparing, and no runner has started yet.
        self.command = command
        self.before = before
        self.runners: list[subprocess.Popen] = []
        self._hub = self

    def available(self) -> bool:
        return True

    def expand(self, state: str) -> Path:
        return Path(state.replace("$HOME", str(self.home)))

    async def precheck(self, session, *, needs_place):
        return Placement(DEVICE, 1, AGENT, rented=False)

    async def ensure_ready(self, *, env, runtime_factory, **_):
        state = self.expand(runtime_factory(TOPIC)["state"])
        state.mkdir(parents=True, exist_ok=True)
        if self.before is not None:
            self.before(state)
        if self.command is not None:
            with (state / "runner.log").open("ab") as log:
                self.runners.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-I",
                            "-S",
                            str(self.artifact),
                            "--state",
                            str(state),
                        ],
                        env={
                            "PATH": os.environ["PATH"],
                            "HOME": str(self.home),
                            "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                            "CHEESE_CLAUDE_COMMAND": self.command,
                            **(env or {}),
                        },
                        stdout=subprocess.DEVNULL,
                        stderr=log,
                    )
                )
        return SimpleNamespace(device_id=DEVICE)

    async def exec(self, device_id, argv, *, timeout=60, **_):
        done = await asyncio.to_thread(
            subprocess.run,
            argv,
            env={"PATH": os.environ["PATH"], "HOME": str(self.home)},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {"stdout": done.stdout, "stderr": done.stderr, "exit": done.returncode}

    async def call_executor(self, device_id, state, method, params, **_):
        path = socket_path(self.expand(state))
        try:
            return await asyncio.to_thread(self._relay, path, method, params)
        except FileNotFoundError:
            raise RuntimeError(
                f"dial unix {path}: connect: no such file or directory"
            ) from None

    @staticmethod
    def _relay(path: str, method: str, params: dict) -> dict:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(10)
            client.connect(path)
            client.sendall(
                json.dumps({"method": method, "params": params}).encode() + b"\n"
            )
            answer = json.loads(client.makefile("rb").readline())
        if "error" in answer:
            raise RuntimeError(answer["error"])
        return answer["result"]

    def stop(self) -> None:
        for runner in self.runners:
            if runner.poll() is None:
                runner.kill()
            runner.wait()


def _dies(reason: str) -> str:
    """A launch whose bootstrap prints its reason and exits, as a refused
    work lease did."""
    return shlex.join(["sh", "-c", f"printf '%s\\n' {shlex.quote(reason)} >&2; exit 1"])


async def _start(host: Host) -> float:
    channel = ClaudeCodeChannel(host)  # type: ignore[arg-type]
    session = SessionRef(PROJECT, TOPIC, AGENT, harness=CLAUDE_CODE)
    started = time.monotonic()
    try:
        with pytest.raises(ScreenSetupError) as refused:
            await channel.ensure(session, Opening(system_prompt=""))
    finally:
        host.stop()
    host.refusal = str(refused.value)
    return time.monotonic() - started


@pytest.mark.anyio
async def test_a_session_that_dies_on_its_way_up_is_reported_at_once(tmp_path):
    reason = "PlatformHTTPError: Platform HTTP 504: 工作机器仍在准备"
    host = Host(tmp_path, _dies(reason))

    waited = await _start(host)

    assert reason in host.refusal
    assert waited < 30, f"the room waited {waited:.0f}s for a runner already gone"


@pytest.mark.anyio
async def test_a_runner_that_fails_itself_is_reported_at_once(tmp_path):
    held = []

    def taken(state: Path) -> None:
        # Another runner holds this state directory.
        lock = (state / "runner.lock").open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(lock)

    host = Host(tmp_path, _dies("never reached"), before=taken)
    try:
        waited = await _start(host)
    finally:
        for lock in held:
            lock.close()

    assert "BlockingIOError" in host.refusal
    assert waited < 30, f"the room waited {waited:.0f}s for a runner already gone"


@pytest.mark.anyio
async def test_an_earlier_launch_ending_does_not_end_this_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_channel, "STARTUP_WAIT_S", 3.0)

    def ended_before(state: Path) -> None:
        (state / "runner.log").write_text(
            "cheese-runner 0123abcd ended: Claude Code exited with status 1 "
            "before it started:\nyesterday's reason\n"
        )

    host = Host(tmp_path, None, before=ended_before)

    waited = await _start(host)

    assert waited >= 3.0, "an earlier launch's ending was taken for this one's"
