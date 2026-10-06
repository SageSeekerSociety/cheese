"""A room that starts Claude Code learns at once when the session has died.

The session host here is this machine: a HOME on disk, the runner archive
started the way the launcher starts it (its stderr appended to ``runner.log``),
and a connector that relays one JSON line to the runner's socket and fails the
way the real one does when nothing is listening. What is checked is how long a
room waits and what it is told, through the room's own ``ensure``.
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
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.channel import Placement
from app.domain.agent.harness.claude_code.bundle import build
from app.domain.agent.harness.driven.runner import socket_path
from app.domain.agent.harness.launch import MachinePlace
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host import claude_code as claude_driver
from app.domain.agent.session_host.contract import StartRefused
from app.domain.agent.session_host.host import SessionHost

PROJECT, TOPIC = uuid.uuid4(), uuid.uuid4()
AGENT = "cheese-agent"
DEVICE = "dev1"


class Host:
    """The room's placement and the session host's screens, on this disk."""

    name = "central"
    deferred_work = False
    _session_factory = None
    # What the room was told when the session did not come up, and the
    # refusal itself (its `log` is what 现场 shows).
    refusal = ""
    refused: StartRefused | None = None

    def __init__(self, root: Path, command: str | None, *, before=None):
        self.home = root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.artifact = root / "runner.pyz"
        self.artifact.write_bytes(build())
        # None: the launcher is still preparing, and no runner has started yet.
        self.command = command
        self.before = before
        self.runners: list[subprocess.Popen] = []
        self.placed = ""

    def available(self) -> bool:
        return True

    def is_online(self, device_id: str) -> bool:
        return device_id == DEVICE

    def place(self, state: str) -> MachinePlace:
        return MachinePlace(
            home=str(self.home),
            workdir=str(self.home),
            store=str(self.home),
            state=state,
            api_base="http://api.test",
            project_id=str(PROJECT),
            topic_id=str(TOPIC),
            agent_handle=AGENT,
        )

    def expand(self, state: str) -> Path:
        return Path(state.replace("$HOME", str(self.home)))

    async def precheck(self, session, *, needs_place):
        return Placement(DEVICE, 1, AGENT, rented=False)

    @asynccontextmanager
    async def prepare_session(
        self, *, session, token, env, precheck, runtime_factory, reading=False
    ):
        self.placed = runtime_factory(TOPIC)["state"]
        yield SimpleNamespace(
            device_id=DEVICE,
            agent_user_id=1,
            agent_handle=AGENT,
            token=token,
            env={
                **(env or {}),
                "CHEESE_RESOURCE_ID": str(TOPIC),
                "CHEESE_EXECUTION_TARGET": json.dumps({"kind": "deferred"}),
            },
        )

    async def _ensure_screen(self, *, env, launch, **_):
        placed = self.placed
        state = self.expand(placed)
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
                            # The launch's own environment, which a channel
                            # merges into the one the runner starts with.
                            **launch.on(self.place(placed)).env,
                        },
                        stdout=subprocess.DEVNULL,
                        stderr=log,
                    )
                )
        return SimpleNamespace(device_id=DEVICE, sid="s1")

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
    room = RoomSessions(
        host,  # type: ignore[arg-type]
        CLAUDE_CODE,
        SessionHost(host, screens=host),  # type: ignore[arg-type]
    )
    session = SessionRef(PROJECT, TOPIC, AGENT, harness=CLAUDE_CODE)
    started = time.monotonic()
    try:
        with pytest.raises(StartRefused) as refused:
            await room.ensure(session, system_prompt="")
    finally:
        host.stop()
    host.refused = refused.value
    host.refusal = str(refused.value)
    return time.monotonic() - started


# What the executor client's bootstrap printed on dev when the work lease
# answered 504 to a relaunched session (2026-09-25), paths shortened.
LEASE_504 = """Traceback (most recent call last):
  File "/h/.cheese/remote-execution/client.py", line 170, in prepare
    target = _take_leased_machine(target)
  File "/h/.cheese/remote-execution/client.py", line 544, in _take_leased_machine
    client.acquire(deadline=time.monotonic())
  File "/h/.cheese/remote-execution/executor_transport.py", line 515, in acquire
    raise PlatformHTTPError(response.status, body)
executor_transport.PlatformHTTPError: Platform HTTP 504: {"code":504}"""


@pytest.mark.anyio
async def test_a_session_that_dies_on_its_way_up_is_reported_at_once(tmp_path):
    host = Host(tmp_path, _dies(LEASE_504))

    waited = await _start(host)

    assert waited < 30, f"the room waited {waited:.0f}s for a runner already gone"
    # The room gets one sentence; what the bootstrap printed goes with it for
    # 现场, and none of it is in the sentence.
    assert host.refusal == "Claude Code 启动失败：这个频道的工作电脑还在准备"
    assert host.refused is not None and host.refused.log
    assert "Platform HTTP 504" in host.refused.log
    assert "status 1" in host.refused.log
    assert "Traceback" not in host.refusal and "HTTP" not in host.refusal


@pytest.mark.anyio
async def test_a_missing_program_is_named_and_its_error_kept_for_the_site(
    tmp_path,
):
    host = Host(tmp_path, "/nonexistent/bin/claude")

    await _start(host)

    assert host.refusal == "Claude Code 启动失败：机器上缺少 Claude Code"
    assert host.refused is not None and "/nonexistent/bin/claude" in host.refused.log
    assert "/nonexistent" not in host.refusal


@pytest.mark.anyio
async def test_a_launch_that_fails_moments_after_it_starts_is_refused(tmp_path):
    # The runner answers its socket as soon as the launch command is running.
    # A bootstrap that fails a moment later must still be reported as a start
    # that failed, not taken for a session that came up.
    reason = "bootstrap: the work lease was refused"
    host = Host(
        tmp_path,
        shlex.join(
            ["sh", "-c", f"sleep 0.3; printf '%s\\n' {shlex.quote(reason)} >&2; exit 1"]
        ),
    )

    await _start(host)

    assert host.refused is not None and reason in host.refused.log


@pytest.mark.anyio
async def test_a_cause_nobody_recognises_is_not_read_out_in_the_room(tmp_path):
    reason = "ValueError: the flux capacitor is out of alignment"
    host = Host(tmp_path, _dies(reason))

    await _start(host)

    assert host.refusal == "Claude Code 启动失败：原因没能识别，启动记录在现场"
    assert host.refused is not None and reason in host.refused.log


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

    assert waited < 30, f"the room waited {waited:.0f}s for a runner already gone"
    assert host.refusal == "Claude Code 启动失败：这个频道上一个会话进程还没有退出"
    assert host.refused is not None and "BlockingIOError" in host.refused.log


@pytest.mark.anyio
async def test_an_earlier_launch_ending_does_not_end_this_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_driver, "STARTUP_WAIT_S", 3.0)

    def ended_before(state: Path) -> None:
        (state / "runner.log").write_text(
            "cheese-runner 0123abcd ended: Claude Code exited with status 1 "
            "before it started:\nyesterday's reason\n"
        )

    host = Host(tmp_path, None, before=ended_before)

    waited = await _start(host)

    assert waited >= 3.0, "an earlier launch's ending was taken for this one's"
    # Nothing ended this launch, so the room hears that it never came up in
    # time, not the earlier launch's reason.
    assert host.refusal == "Claude Code 启动失败：在等待时限内没有起来，启动记录在现场"
