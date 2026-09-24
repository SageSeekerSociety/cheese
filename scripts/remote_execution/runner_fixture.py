"""A Claude Code session held by the runner, the way a room holds one.

The session starts as a room's does: the runner archive
(`claude_code.bundle.build`) runs the command a room's launcher hands it in
`CHEESE_CLAUDE_COMMAND`, which is the remote-execution client's `bootstrap` in
front of the pinned binary and `LAUNCH_ARGS`. After that, everything goes
through the runner's socket. What the session did is read from the runner's
journal: a turn has ended at its `result` record, and a tool ran when its
`tool_result` is there.

Needs the backend's environment (`backend/.venv`): the archive, the launch
arguments and the helper release come from the backend source tree.
"""

import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.agent.harness.claude_code.bundle import build  # noqa: E402
from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS  # noqa: E402
from app.domain.agent.harness.claude_code.remote_execution import (  # noqa: E402
    release,
)
from app.domain.agent.harness.claude_code.session_launch import (  # noqa: E402
    session_settings,
)
from app.domain.agent.harness.driven.journal import PAGE  # noqa: E402
from app.domain.agent.harness.driven.runner import socket_path  # noqa: E402


def room_home(home, target):
    """Lay out a session home as the device launcher does; return its environment.

    The helpers go where the launcher writes them (`.cheese/remote-execution`,
    with the release marker it writes), the target beside them, and the room's
    settings into the config dir, which `bootstrap` extends.
    """
    helpers = home / ".cheese/remote-execution"
    helpers.mkdir(parents=True, exist_ok=True)
    sources = release.sources()
    for name, source in sources.items():
        (helpers / name).write_text(source)
    (helpers / "release-ready").write_text(release.digest(sources))
    (home / ".cheese/remote-target.json").write_text(json.dumps(target))
    config = home / ".claude"
    config.mkdir(exist_ok=True)
    (config / "settings.json").write_text(json.dumps(session_settings()))
    return {
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(config),
        "CHEESE_WORK": str(home / ".cheese/remote-session/workspace"),
    }


def room_command(home, claude, extra_args=()):
    """The command the launcher hands the runner, for a home `room_home` laid out."""
    return shlex.join(
        [
            sys.executable,
            str(home / ".cheese/remote-execution/client.py"),
            "bootstrap",
            str(home / ".cheese/remote-target.json"),
            claude,
            *extra_args,
            *LAUNCH_ARGS,
        ]
    )


def records_of(entries, kind):
    return [entry["record"] for entry in entries if entry["record"].get("type") == kind]


def blocks(record):
    content = (record.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


class Session:
    """The runner at `state`, and the process that holds it."""

    def __init__(self, state, process, log):
        self.state = Path(state)
        self.process = process
        self.log = Path(log)
        self.call("ping", timeout=60)

    @classmethod
    def start(cls, folder, command, env, cwd, name="runner"):
        """Start the runner archive on `command`, as the launcher does."""
        archive = folder / f"{name}.pyz"
        archive.write_bytes(build())
        state = folder / f"{name}-state"
        log = folder / f"{name}.log"
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", str(archive), "--state", str(state)],
            cwd=cwd,
            env={**env, "CHEESE_CLAUDE_COMMAND": command},
            stdin=subprocess.DEVNULL,
            stdout=log.open("ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return cls(state, process, log)

    def _failed(self, why):
        tail = self.log.read_text(errors="replace")[-3000:] if self.log.exists() else ""
        claude = self.state / "claude.log"
        if claude.exists():
            tail += (
                "\n--- claude.log ---\n" + claude.read_text(errors="replace")[-3000:]
            )
        return RuntimeError(f"{why}\n{tail}")

    def call(self, method, params=None, timeout=660):
        """One request on the runner's socket, as the connector relays it."""
        deadline = time.monotonic() + timeout
        while True:
            connection = socket.socket(socket.AF_UNIX)
            try:
                connection.settimeout(timeout)
                connection.connect(socket_path(self.state))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                connection.close()
                if self.process.poll() is not None:
                    raise self._failed(f"The runner exited ({self.process.returncode})")
                if time.monotonic() > deadline:
                    raise self._failed("The runner never opened its socket")
                time.sleep(0.1)
        with connection:
            connection.sendall(
                json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
            )
            answer = json.loads(connection.makefile("rb").readline())
        if "error" in answer:
            raise RuntimeError(f"{method}: {answer['error']}")
        return answer["result"]

    def entries(self, after=0):
        """The journal from `after`, every page of it."""
        found = []
        while True:
            page = self.call("events", {"after": after})["events"]
            found += page
            if len(page) < PAGE:
                return found
            after = page[-1]["sequence"]

    def mark(self):
        entries = self.entries()
        return entries[-1]["sequence"] if entries else 0

    def wait(self, predicate, after=0, timeout=90):
        """The first journal record from `after` that matches."""
        deadline = time.monotonic() + timeout
        while True:
            for entry in self.entries(after):
                if predicate(entry["record"]):
                    return entry["record"]
            if self.process.poll() is not None:
                raise self._failed(f"The runner exited ({self.process.returncode})")
            if time.monotonic() > deadline:
                raise self._failed("Timed out waiting on the journal")
            time.sleep(0.2)

    def turn(self, text, timeout=90):
        """Send a message and wait for the `result` that ends its turn."""
        after = self.mark()
        work = str(uuid.uuid4())
        self.call(
            "send", {"input_id": str(uuid.uuid4()), "work_id": work, "text": text}
        )
        return self.wait(
            lambda record: (
                record.get("type") == "result"
                and (record.get("cheese") or {}).get("work_id") == work
            ),
            after,
            timeout,
        )

    def tool_results(self, after=0):
        """Every tool_result the session recorded on its main thread, by call id."""
        return {
            block["tool_use_id"]: block
            for record in records_of(self.entries(after), "user")
            if record.get("parent_tool_use_id") is None
            for block in blocks(record)
            if block.get("type") == "tool_result"
        }

    def stop(self, receipt=None):
        """End the runner, which ends the session; keep its journal as a receipt."""
        if receipt is not None and self.process.poll() is None:
            try:
                with Path(receipt).open("w") as output:
                    for entry in self.entries():
                        output.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except (OSError, RuntimeError):
                pass
        # The whole group: under the device launcher the runner is a child of
        # the launcher's shell, and it is the runner that ends the session.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if self.process.poll() is not None:
                return
            try:
                os.killpg(self.process.pid, sig)
                self.process.wait(30)
            except ProcessLookupError:
                return
            except subprocess.TimeoutExpired:
                continue
