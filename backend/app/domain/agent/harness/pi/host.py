"""Install and start a room's pi runner on the central session host.

The launch (`launch.py`) ships this archive and a payload over the connector's
Python stdin and calls `configure`, which leaves exactly one runner for the
room's seat running and answers with its status. A runner already running for
the seat is kept while it was started with the same launch (`contract`), and
while it is working even when it was not: closing a session ends whatever it is
doing, so a changed launch waits for the first turn that finds it idle, and
that turn starts it again on the session's own id. The answer names the launch
the runner left running was started with (``contract``), so the backend knows
when a kept runner is still behind.

Standard library only: this runs in the runner archive on the session host.
"""

import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from app.domain.agent.harness.driven.runner import socket_path

#: How long a start waits for a new runner to answer: it binds its socket only
#: after pi is up and the project's MCP servers are listed.
STARTUP_S = 150.0
#: How long a runner being replaced gets to end on its own.
STOP_S = 10.0


def exchange(state: Path, method: str, params: dict) -> dict:
    connection = socket.socket(socket.AF_UNIX)
    connection.settimeout(5)
    try:
        connection.connect(socket_path(state))
        connection.sendall(
            json.dumps({"method": method, "params": params}).encode() + b"\n"
        )
        with connection.makefile("rb") as stream:
            response = json.loads(stream.readline())
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]
    finally:
        connection.close()


def ping(state: Path) -> dict | None:
    try:
        result = exchange(state, "ping", {})
    except (OSError, RuntimeError, ValueError):
        return None
    return result if result.get("alive") else None


def _stop(state: Path, pid: int) -> None:
    """End the runner a changed launch replaces, and wait for its socket."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + STOP_S
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _install(script: str) -> str:
    """Leave the pinned pi installed on this host; where it is."""
    done = subprocess.run(
        ["sh", "-c", script + '\nprintf "%s" "$PI_BIN"'],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or "pi could not be installed")
    return done.stdout.strip()


def configure(payload: dict) -> dict:
    state = (
        Path(payload["state"].replace("$HOME", str(Path.home()))).expanduser().resolve()
    )
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "bootstrap.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        running = ping(state)
        config = payload["config"]
        if running:
            try:
                previous = json.loads((state / "runner.json").read_text())
            except (OSError, ValueError):
                previous = {}
            resume = config["opening"].get("resume_token")
            if resume and resume != running.get("session_id"):
                raise RuntimeError("The running session cannot resume a different one")
            if (
                previous.get("contract") == config["contract"]
                or running.get("working")
                or running.get("tasks")
            ):
                return {**running, "contract": previous.get("contract", "")}
            _stop(state, int(running["pid"]))
        binary = _install(payload["install"])
        agent = state / "agent"
        agent.mkdir(exist_ok=True, mode=0o700)
        (agent / "models.json").write_text(payload["models"])
        home = state / "home"
        home.mkdir(exist_ok=True, mode=0o700)
        cwd = state / "workspace"
        cwd.mkdir(exist_ok=True)
        config_path = state / "runner.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        artifact = Path(payload["artifact"])
        env = {
            "PATH": os.environ.get("PATH", os.defpath),
            **payload["env"],
            "HOME": str(home),
            # pi reads and writes its config under PI_CODING_AGENT_DIR and never
            # falls back to this host's ~/.pi; one per seat, since every room's
            # session shares this host.
            "PI_CODING_AGENT_DIR": str(agent),
            # Nothing a room waits on may depend on reaching pi.dev.
            "PI_OFFLINE": "1",
            "PI_TELEMETRY": "0",
        }
        with (state / "runner.log").open("ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(artifact),
                    "--state",
                    str(state),
                    "--config",
                    str(config_path),
                    "--binary",
                    binary,
                    "--cwd",
                    str(cwd),
                ],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        deadline = time.monotonic() + STARTUP_S
        while process.poll() is None:
            running = ping(state)
            if running:
                return {**running, "contract": config["contract"]}
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"pi has not answered yet; see {state / 'runner.log'}"
                )
            time.sleep(0.1)
        log_tail = (
            (state / "runner.log").read_bytes()[-1200:].decode("utf-8", "replace")
        )
        raise RuntimeError(log_tail.strip() or "pi exited on startup")
