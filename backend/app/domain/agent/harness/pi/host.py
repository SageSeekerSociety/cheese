"""Install and start a pi runner on the central session host.

The launch (`launch.py`) ships this archive and a payload over the connector's
Python stdin and calls `configure`, which leaves exactly one runner for the
room's seat running and answers with its status. A runner already running for
the seat is kept while it was started with the same launch (`contract`), and
while it is working even when it was not: closing a session ends whatever it is
doing, so a changed launch waits for the first turn that finds it idle, and
that turn starts it again on the session's own id. The answer names the launch
the runner left running was started with (``contract``), so the backend knows
when a kept runner is still behind.

A person's 芝士 is started the same way, one runner per conversation, with two
things a room's launch does not ask for: how many of that person's sessions may
run at once (`group_limit`, `_make_room`), and the memory each may use
(`memory_max`, `_capped`).

Standard library only: this runs in the runner archive on the session host.
"""

import contextlib
import fcntl
import json
import os
import shutil
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
        with contextlib.ExitStack() as held:
            if limit := payload.get("group_limit"):
                group = held.enter_context((state.parent / "group.lock").open("a"))
                fcntl.flock(group, fcntl.LOCK_EX)
                _make_room(state, int(limit))
            return _start(state, payload, config)


def _make_room(state: Path, limit: int) -> None:
    """Leave room for this session among its siblings — a person's other
    sessions, one directory up — by letting the least recently used go until at
    most ``limit`` run with this one. A session that is working is never let go:
    that would end an answer a person is waiting on, so a person asking in more
    places at once than the limit runs over it until those answers are done.
    A session let go keeps its conversation on disk, and its next question
    starts it again on it."""
    running = []
    for sibling in state.parent.iterdir():
        if sibling == state or not sibling.is_dir():
            continue
        if status := ping(sibling):
            running.append((sibling, status))
    surplus = len(running) + 1 - limit
    idle = sorted(
        (
            (sibling, status)
            for sibling, status in running
            if not status.get("working") and not status.get("tasks")
        ),
        key=lambda each: float(each[1].get("idle_s") or 0),
        reverse=True,
    )
    for sibling, status in idle[: max(0, surplus)]:
        _stop(sibling, int(status["pid"]))


#: What ``systemd-run --user`` needs to find the user's manager; the runner's own
#: environment is built from nothing, so these are carried over for it.
_USER_MANAGER = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")


def _capped(command: list[str], memory: str | None, env: dict[str, str]) -> list[str]:
    """The runner's command, under a memory cap of its own when it has one
    (#1544): pi and the runner in a systemd scope that cannot grow past
    ``memory`` or into swap, so one session filling up stops at its cap instead
    of taking every session on the host down with it. A host that cannot make a
    user scope runs it as before, and says so in the runner's log. ``--scope``
    execs the command itself, so the process started is still the runner."""
    if not memory or not shutil.which("systemd-run", path=env.get("PATH")):
        return command
    try:
        probe = subprocess.run(
            ["systemd-run", "--user", "--scope", "--quiet", "true"],
            capture_output=True,
            timeout=10,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return command
    if probe.returncode != 0:
        return command
    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        f"MemoryMax={memory}",
        "-p",
        "MemorySwapMax=0",
        "--",
        *command,
    ]


def _start(state: Path, payload: dict, config: dict) -> dict:
    binary = _install(payload["install"])
    agent = state / "agent"
    agent.mkdir(exist_ok=True, mode=0o700)
    (agent / "models.json").write_text(payload["models"])
    if payload.get("settings"):
        # pi's own settings, for a launch that has any (a person's 芝士 bounds
        # how far its context grows before pi compacts it).
        (agent / "settings.json").write_text(payload["settings"])
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
    command = [
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
    ]
    memory = payload.get("memory_max")
    if memory:
        env.update({k: os.environ[k] for k in _USER_MANAGER if k in os.environ})
    capped = _capped(command, memory, env)
    with (state / "runner.log").open("ab") as log:
        if memory and capped is command:
            log.write(
                b"cheese: no user systemd scope here; "
                b"the session runs without its memory cap\n"
            )
            log.flush()
        process = subprocess.Popen(
            capped,
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
            raise TimeoutError(f"pi has not answered yet; see {state / 'runner.log'}")
        time.sleep(0.1)
    log_tail = (state / "runner.log").read_bytes()[-1200:].decode("utf-8", "replace")
    raise RuntimeError(log_tail.strip() or "pi exited on startup")
