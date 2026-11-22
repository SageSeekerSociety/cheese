"""A language server beside a task's checkout, through the executor's own socket.

What the page's code intelligence stands on: a framed request reaches the
server and its framed answer comes back intact, however the bytes are cut;
the server runs in the task's checkout under the person's name; the
connection and the server end together; a language nobody serves, or a
program the machine lacks, is refused; and a server running there is not
somebody using the machine — it holds off neither an upgrade nor the idle
stop, and goes when the executor does.

The server is a stand-in (`tests/support/fake_language_server.py`) installed
on the machine's ``PATH`` under the name the registry starts.
"""

import json
import os
import socket
import subprocess
import sys
import time
import uuid

import pytest

from app.domain.agent.harness.claude_code.remote_execution import runtime_core
from tests.support import executor_release, fake_language_server

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the executor's socket is a Unix socket here"
)

frame = fake_language_server.frame


def _start(runtime, state, home, bin_dir, *, idle_s=None):
    env = dict(os.environ)
    if idle_s is not None:
        env["CHEESE_EXECUTOR_IDLE_EXIT_S"] = str(idle_s)
    subprocess.run(
        [sys.executable, str(runtime), "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(home / "room"),
                "env": {"HOME": str(home), "PATH": str(bin_dir)},
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
        env=env,
    )


class _Machine:
    def __init__(self, tmp_path, *, idle_s=None):
        self.runtime = executor_release.install(tmp_path / "platform")
        self.home = tmp_path / "home"
        self.task = uuid.uuid4()
        self.checkout = self.home / ".cheese" / "tasks" / str(self.task)
        self.checkout.mkdir(parents=True)
        (self.home / "room").mkdir()
        self.bin = tmp_path / "bin"
        self.program = fake_language_server.install(self.bin)
        self.state = tmp_path / "state"
        _start(self.runtime, self.state, self.home, self.bin, idle_s=idle_s)

    def stop(self):
        subprocess.run(
            [sys.executable, str(self.runtime), "stop", "--state", str(self.state)],
            capture_output=True,
            timeout=30,
        )

    def request(self, method, params):
        return runtime_core.request(self.state, method, params)

    def language(self, language="c", owner="alice", attachment=None):
        connection = runtime_core.connect(self.state)
        connection.settimeout(15)
        header = {
            "method": "language",
            "params": {
                "owner": owner,
                "task_id": str(self.task),
                "in_workspace": False,
                "caller": {
                    "attachment": attachment or str(uuid.uuid4()),
                    "conversation": str(self.task),
                    "author": owner,
                },
                "language": language,
            },
        }
        connection.sendall(json.dumps(header).encode() + b"\n")
        return _Client(connection)


@pytest.fixture
def machine(tmp_path):
    machine = _Machine(tmp_path)
    try:
        yield machine
    finally:
        machine.stop()


class _Client:
    def __init__(self, connection):
        self.connection = connection
        self.buffer = bytearray()

    def _more(self):
        chunk = self.connection.recv(65536)
        if not chunk:
            raise EOFError
        self.buffer.extend(chunk)

    def answer(self):
        while b"\n" not in self.buffer:
            self._more()
        line, _, rest = bytes(self.buffer).partition(b"\n")
        self.buffer[:] = rest
        return json.loads(line)

    def message(self):
        while b"\r\n\r\n" not in self.buffer:
            self._more()
        head, _, rest = bytes(self.buffer).partition(b"\r\n\r\n")
        length = int(head.split(b":", 1)[1])
        while len(rest) < length:
            self._more()
            head, _, rest = bytes(self.buffer).partition(b"\r\n\r\n")
        self.buffer[:] = rest[length:]
        return json.loads(rest[:length])

    def ask(self, method, params=None, request_id=1):
        self.connection.sendall(
            frame(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )
        )
        return self.message()

    def ended(self, within=10.0):
        """Whether the far side closes the connection within ``within``."""
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            try:
                chunk = self.connection.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                return True
            if not chunk:
                return True
        return False

    def close(self):
        self.connection.close()


def _gone(pid, within=10.0):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        stat = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        ).stdout.strip()
        if not stat or stat.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def test_a_request_reaches_the_server_and_its_answer_comes_back(machine):
    client = machine.language()
    assert client.answer() == {"result": {"root": str(machine.checkout.resolve())}}

    assert client.ask("echo", {"text": "héllo"})["result"] == {"text": "héllo"}

    # Cut anywhere, two requests in pieces arrive as two requests.
    data = frame({"jsonrpc": "2.0", "id": 2, "method": "echo", "params": [2]})
    data += frame({"jsonrpc": "2.0", "id": 3, "method": "echo", "params": [3]})
    for at in range(0, len(data), 7):
        client.connection.sendall(data[at : at + 7])
        time.sleep(0.001)
    assert [client.message()["result"], client.message()["result"]] == [[2], [3]]
    client.close()


def test_the_server_runs_in_the_checkout_as_the_person(machine):
    attachment = str(uuid.uuid4())
    client = machine.language(attachment=attachment)
    client.answer()
    who = client.ask("whoami")["result"]
    assert who["cwd"] == str(machine.checkout.resolve())
    assert (who["attachment"], who["author"]) == (attachment, "alice")
    client.close()


def test_closing_the_connection_ends_the_server(machine):
    client = machine.language()
    client.answer()
    pid = client.ask("whoami")["result"]["pid"]
    client.close()
    assert _gone(pid)


def test_the_server_ending_closes_the_connection(machine):
    client = machine.language()
    client.answer()
    client.connection.sendall(frame({"jsonrpc": "2.0", "method": "exit"}))
    assert client.ended()


def test_a_language_nobody_serves_is_unavailable(machine):
    assert machine.language("cobol").answer() == {"error": "unavailable"}


def test_a_server_whose_program_is_missing_is_unavailable(machine):
    machine.program.unlink()
    assert machine.language("cpp").answer() == {"error": "unavailable"}


def test_a_person_runs_at_most_two_servers_for_a_task(machine):
    first, second = machine.language(), machine.language("cpp")
    assert "result" in first.answer() and "result" in second.answer()
    assert machine.language().answer() == {"error": "limit"}
    # Somebody else's are their own.
    assert "result" in machine.language(owner="bob").answer()
    first.close()
    deadline = time.monotonic() + 10
    while True:
        answer = machine.language().answer()
        if "result" in answer or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert "result" in answer


def test_a_running_language_server_does_not_hold_off_an_upgrade(machine):
    client = machine.language()
    client.answer()
    client.ask("echo", [1])
    assert machine.request("begin_upgrade", {"release": "next"})["ready"] is True
    client.close()


def test_a_running_language_server_does_not_keep_the_executor_up(tmp_path):
    machine = _Machine(tmp_path, idle_s=1)
    try:
        client = machine.language()
        client.answer()
        pid = client.ask("whoami")["result"]["pid"]
        # The executor stops for sitting idle all the same, and the server
        # and its connection go with it.
        assert client.ended(within=20)
        assert _gone(pid)
        assert not os.path.exists(runtime_core.socket_path(machine.state))
    finally:
        machine.stop()


def test_what_the_server_writes_to_stderr_does_not_reach_the_client(machine):
    client = machine.language()
    client.answer()
    assert client.ask("echo", "x")["result"] == "x"
    client.connection.settimeout(0.5)
    with pytest.raises((TimeoutError, socket.timeout)):
        client.connection.recv(1)
    client.close()
