"""The session host for a test: this machine, reached the way the connector
reaches the real one.

A launch script runs here as ``python3 -`` with the test's own home, so the
runner and the pinned pi it starts live under it; a call to a runner is one
JSON line each way over its socket, as ``cli/internal/host/executor.go`` relays
it. Every command the platform runs on the host is recorded (``execs``).
"""

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

from app.domain.agent.harness.driven.runner import socket_path
from app.domain.agent.harness.pi.launch import VERSION
from tests.pinned_claude import pi_binary

DEVICE = "center"


def install_pi(home: Path) -> None:
    """The pinned pi, where a launch looks for it before downloading it."""
    pin = home / ".cheese/tools/pi"
    pin.mkdir(parents=True)
    (pin / VERSION).symlink_to(Path(pi_binary()).parent)


class Host:
    """The session host, as the connector reaches it: a command run here with
    this test's home, and one JSON line each way to a runner's socket."""

    def __init__(self, home: Path):
        self.home = home
        self.execs: list[list[str]] = []

    def is_online(self, device_id: str) -> bool:
        return device_id == DEVICE

    async def exec(
        self, device_id, argv, *, cwd=None, env=None, timeout=60, stdin=None
    ):
        self.execs.append(list(argv))
        process = await asyncio.create_subprocess_exec(
            *([sys.executable, "-"] if argv == ["python3", "-"] else argv),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "HOME": str(self.home)},
        )
        out, err = await asyncio.wait_for(
            process.communicate((stdin or "").encode()), timeout
        )
        return {
            "stdout": out.decode(),
            "stderr": err.decode(),
            "exit": process.returncode,
        }

    def state(self, recorded: str) -> Path:
        return Path(recorded.replace("$HOME", str(self.home))).resolve()

    async def call_executor(self, device_id, state, method, params, *, timeout=660):
        from app.domain.agent.device_hub import DeviceCallError

        try:
            reader, writer = await asyncio.open_unix_connection(
                socket_path(self.state(state)), limit=2**24
            )
        except OSError as exc:
            raise DeviceCallError(str(exc)) from exc
        try:
            writer.write(
                json.dumps({"method": method, "params": params}).encode() + b"\n"
            )
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout)
        finally:
            writer.close()
        answer = json.loads(line or b"{}")
        if "error" in answer or "result" not in answer:
            raise DeviceCallError(answer.get("error") or "the runner said nothing")
        return answer["result"]

    def alive(self, recorded: str) -> bool:
        connection = socket.socket(socket.AF_UNIX)
        try:
            connection.connect(socket_path(self.state(recorded)))
        except OSError:
            return False
        finally:
            connection.close()
        return True


def stop_all(home: Path) -> None:
    from app.domain.agent.harness.pi.host import _stop, ping

    for state in home.glob(".cheese/personal/*/*"):
        if status := ping(state):
            _stop(state, int(status["pid"]))
