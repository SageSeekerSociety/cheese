"""What a session machine's runner promises, on Linux, macOS and Windows alike.

These run without the backend's dependencies, because on a member's Windows
machine there are none (#2991): the `windows` job of
`.github/workflows/remote-execution.yml` runs this directory on Windows with
plain Python and pytest, `--noconftest`.

- The session's own helpers reach the runner the way they dial it
  (`executor_transport.dial_runner`), and on Windows, where the runner listens
  on a loopback port anyone on the machine can reach, nobody else does.
- One runner holds a state directory; a second is refused.
- A helper that runs a program in its place ends the way that program ended,
  so the runner waiting on it sees the session end when it ends.
"""

import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

# As in the runner's archive (`driven/bundle.py`), where portable.py, which the
# runner imports on Windows, sits at the root.
if sys.platform == "win32":
    sys.path.insert(
        0,
        str(
            Path(__file__).resolve().parents[3]
            / "app/domain/agent/harness/claude_code/remote_execution"
        ),
    )

from app.domain.agent.executor_transport import dial_runner  # noqa: E402
from app.domain.agent.harness.driven import runner  # noqa: E402
from app.domain.agent.harness.driven.journal import Journal  # noqa: E402

CLIENT = (
    Path(__file__).resolve().parents[3]
    / "app/domain/agent/harness/claude_code/remote_execution/client.py"
)


class _Journal(Journal):
    schema = "CREATE TABLE IF NOT EXISTS facts (k TEXT PRIMARY KEY, v TEXT);"


class _Echo(runner.Runner):
    async def dispatch(self, method, params):
        return {"method": method, "params": params}


def _ask(path: str, request: dict) -> dict:
    with dial_runner(path, 10) as connection:
        connection.sendall(json.dumps(request).encode() + b"\n")
        return json.loads(connection.makefile("rb").readline())


def test_the_session_reaches_its_runner(tmp_path):
    async def run():
        held = _Echo(tmp_path / "state", _Journal, "journal.db")
        held.claim()
        await held.listen(1 << 16)
        try:
            path = runner.socket_path(held.state)
            return await asyncio.to_thread(_ask, path, {"method": "ping"})
        finally:
            held.server.close()

    assert asyncio.run(run()) == {"result": {"method": "ping", "params": {}}}


def test_nobody_else_on_the_machine_reaches_the_runner(tmp_path):
    async def run():
        held = _Echo(tmp_path / "state", _Journal, "journal.db")
        held.claim()
        await held.listen(1 << 16)
        try:
            path = Path(runner.socket_path(held.state))
            if sys.platform != "win32":
                # Only the user who started the runner may connect to it.
                return {"others may connect": path.stat().st_mode & 0o077 != 0}
            endpoint = json.loads(path.read_text())

            def stranger():
                with socket.create_connection(("127.0.0.1", endpoint["port"]), 10) as c:
                    c.sendall(
                        b"not-the-token\n"
                        + json.dumps({"method": "ping"}).encode()
                        + b"\n"
                    )
                    return json.loads(c.makefile("rb").readline())

            return await asyncio.to_thread(stranger)
        finally:
            held.server.close()

    answer = asyncio.run(run())
    if sys.platform != "win32":
        assert answer == {"others may connect": False}
    else:
        assert "result" not in answer and "error" in answer


def test_one_runner_holds_a_state_directory(tmp_path):
    first = _Echo(tmp_path / "state", _Journal, "journal.db")
    first.claim()
    second = _Echo(tmp_path / "state", _Journal, "journal2.db")
    with pytest.raises(BlockingIOError):
        second.claim()


def test_a_program_run_in_place_ends_the_way_it_ended(tmp_path):
    script = (
        "import runpy, sys\n"
        # As on the machine, where it runs from its own directory.
        f"sys.path.insert(0, {str(CLIENT.parent)!r})\n"
        f"helper = runpy.run_path({str(CLIENT)!r})\n"
        "helper['run_in_place']([sys.executable, '-c', 'raise SystemExit(7)'])\n"
    )
    finished = subprocess.run([sys.executable, "-c", script], timeout=60)
    assert finished.returncode == 7
