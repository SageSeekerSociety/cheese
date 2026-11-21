"""A person's shells on the machine, through the executor's own socket.

What a person sees in the 终端 tab is what this protocol carries: a keystroke
reaches their shell and its output comes back; the shell outlives the
connection that watched it; another person neither sees it nor reaches it;
and a person keeps at most eight shells running at once.
"""

import json
import os
import struct
import subprocess
import sys
import time
import uuid

import pytest

from app.domain.agent.harness.claude_code.remote_execution import runtime_core
from tests.support import executor_release

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="a terminal is a POSIX pseudo-terminal"
)

FRAME = struct.Struct(">cI")


@pytest.fixture
def machine(tmp_path):
    runtime = executor_release.install(tmp_path / "platform")
    home = tmp_path / "home"
    task = uuid.uuid4()
    checkout = home / ".cheese" / "tasks" / str(task)
    checkout.mkdir(parents=True)
    (home / "room").mkdir()
    state = tmp_path / "state"
    subprocess.run(
        [sys.executable, str(runtime), "start", "--state", str(state)],
        input=json.dumps({"workspace": str(home / "room"), "env": {"HOME": str(home)}}),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    try:
        yield _Machine(runtime_core, state, task, checkout)
    finally:
        subprocess.run(
            [sys.executable, str(runtime), "stop", "--state", str(state)],
            capture_output=True,
            timeout=30,
        )


class _Machine:
    def __init__(self, core, state, task, checkout):
        self.core, self.state, self.task, self.checkout = core, state, task, checkout

    def request(self, method, params):
        return self.core.request(self.state, method, params)

    def terminal(self, owner, terminal_id):
        connection = self.core.connect(self.state)
        header = {
            "method": "terminal",
            "params": {
                "owner": owner,
                "id": terminal_id,
                "task_id": str(self.task),
                "caller": {"attachment": str(uuid.uuid4()), "author": owner},
                "cols": 100,
                "rows": 30,
            },
        }
        connection.sendall(json.dumps(header).encode() + b"\n")
        return _Terminal(connection)


class _Terminal:
    def __init__(self, connection):
        self.connection = connection
        self.connection.settimeout(15)
        self.buffer = bytearray()
        self.output = bytearray()
        self.statuses = []
        self.exit = None

    def type(self, text):
        payload = text.encode()
        self.connection.sendall(FRAME.pack(b"i", len(payload)) + payload)

    def _frame(self):
        while True:
            if len(self.buffer) >= FRAME.size:
                kind, length = FRAME.unpack_from(self.buffer)
                if len(self.buffer) >= FRAME.size + length:
                    payload = bytes(self.buffer[FRAME.size : FRAME.size + length])
                    del self.buffer[: FRAME.size + length]
                    return kind, payload
            chunk = self.connection.recv(65536)
            if not chunk:
                return None, b""
            self.buffer.extend(chunk)

    def until(self, seen, timeout=15):
        """Read frames until ``seen(self)`` holds."""
        deadline = time.monotonic() + timeout
        while not seen(self):
            assert time.monotonic() < deadline, bytes(self.output)[-2000:]
            kind, payload = self._frame()
            if kind is None:
                break
            if kind == b"o":
                self.output.extend(payload)
            elif kind == b"s":
                self.statuses.append(json.loads(payload))
            elif kind == b"x":
                self.exit = int(payload)
        return self

    def text(self):
        return bytes(self.output).decode(errors="replace")

    def close(self):
        self.connection.close()


def _attached(term):
    return any("attached" in s or "refused" in s for s in term.statuses)


def test_a_keystroke_reaches_the_shell_and_its_output_comes_back(machine):
    term = machine.terminal("alice", "t1").until(_attached)
    term.type("pwd\n")
    term.until(lambda t: str(machine.checkout.resolve()) in t.text())
    term.close()


def test_a_shell_outlives_its_watcher_and_shows_what_it_wrote(machine):
    first = machine.terminal("alice", "t1").until(_attached)
    first.type("echo marker-$((40 + 2))\n")
    first.until(lambda t: "marker-42" in t.text())
    first.type("echo $$ > pid\n")
    first.close()

    again = machine.terminal("alice", "t1").until(_attached)
    again.until(lambda t: "marker-42" in t.text())
    again.type("echo same-$(cat pid)-$$\n")
    again.until(lambda t: "same-" in t.text() and t.text().count("same-") >= 2)
    line = [ln for ln in again.text().splitlines() if ln.startswith("same-")][-1]
    _, before, now = line.strip().split("-")
    assert before == now
    assert machine.request("terminal_list", {"owner": "alice"}) == {
        "terminals": [{"id": "t1", "ended": False}]
    }
    again.close()


def test_another_person_neither_sees_nor_reaches_a_shell(machine):
    alice = machine.terminal("alice", "t1").until(_attached)
    alice.type("echo alice-secret-$((1 + 1))\n")
    alice.until(lambda t: "alice-secret-2" in t.text())

    assert machine.request("terminal_list", {"owner": "bob"}) == {"terminals": []}
    bob = machine.terminal("bob", "t1").until(_attached)
    bob.type("echo bob-here\n")
    bob.until(lambda t: "bob-here" in t.text() and t.text().count("bob-here") >= 2)
    assert "alice-secret" not in bob.text()
    alice.close()
    bob.close()


def test_a_ninth_shell_is_refused_and_an_ended_one_does_not_count(machine):
    terms = [machine.terminal("alice", f"t{n}").until(_attached) for n in range(8)]
    assert all({"refused"} & set(t.statuses[0]) == set() for t in terms)

    ninth = machine.terminal("alice", "t8").until(_attached)
    assert ninth.statuses[0] == {"refused": "limit"}

    terms[0].type("exit\n")
    terms[0].until(lambda t: t.exit is not None)
    assert {"id": "t0", "ended": True} in machine.request(
        "terminal_list", {"owner": "alice"}
    )["terminals"]
    again = machine.terminal("alice", "t8").until(_attached)
    assert "attached" in again.statuses[0]
    for term in [*terms, again]:
        term.close()


def test_closing_a_shell_ends_it(machine):
    term = machine.terminal("alice", "t1").until(_attached)
    term.type("echo $$ > pid\n")
    pid_file = machine.checkout / "pid"
    deadline = time.monotonic() + 15
    while not pid_file.exists() or not pid_file.read_text().strip():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    pid = int(pid_file.read_text())

    assert machine.request("terminal_close", {"owner": "alice", "id": "t1"}) == {
        "closed": True
    }
    assert machine.request("terminal_list", {"owner": "alice"}) == {"terminals": []}
    deadline = time.monotonic() + 5
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "the shell is still running"
        time.sleep(0.05)
    term.close()


def test_an_executor_offers_a_terminal(machine):
    assert "terminal" in machine.request("ping", {})["capabilities"]
