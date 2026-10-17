"""A Bash call asks the platform for its machine once, not twice.

A Claude Code room's Bash call is checked against the project's
`permissions.deny` on the room's machine (`proxy.js` → `permission`), and the
command is then started there by another process (`shell_prefix`). Both used to
ask the platform for the session's hands, one right after the other. The
start now goes on the admission its permission check just took; a start that
fails on it takes the hands again and is sent once more (the command id makes
it the same start), and a start long after any admission asks as before.
"""

import json

from app.domain.agent import executor_transport
from app.domain.agent.executor_transport import RemoteClient

LEASE = {
    "data": {
        "target": {
            "kind": "device",
            "url": "http://executor.test",
            "generation": "g1",
            "workspace": "/work",
        },
        "token": "execution-only",
    }
}
START = {
    "subtype": "shell",
    "operation": "start",
    "kind": "bash",
    "command_id": "shell-1",
    "body": "true",
}


class Room:
    """The platform and the room's machine behind one fake connection: what
    each was asked, in order (``lease`` for the platform)."""

    def __init__(self, monkeypatch, *, refuse_starts=0):
        self.asked = []
        self.refuse_starts = refuse_starts
        room = self

        class Connection:
            sock = None

            def request(self, method, path, *, body, headers):
                sent = json.loads(body)
                if "method" not in sent:
                    room.asked.append("lease")
                    self.status, self.body = 200, LEASE
                    return
                params = sent["params"]
                asked = params.get("operation") or params.get("subtype")
                room.asked.append(asked)
                if asked == "start":
                    room.cwd = params.get("cwd")
                if asked == "start" and room.refuse_starts:
                    room.refuse_starts -= 1
                    self.status, self.body = 409, {"message": "not current"}
                else:
                    self.status, self.body = 200, {}

            def getresponse(self):
                return self

            def getheader(self, _name, default=None):
                return default

            def read(self):
                return json.dumps(self.body).encode()

            def close(self):
                pass

        def connection(self):
            self.transport.headers = {}
            return Connection(), ""

        monkeypatch.setattr(RemoteClient, "connection", connection)
        monkeypatch.setenv("CHEESE_API", "http://platform.test")


def _session(tmp_path):
    (tmp_path / "execution.token").write_text("execution-only")
    target = tmp_path / "execution.json"
    config = {
        "kind": "device",
        "url": "http://executor.test",
        "generation": "g1",
        "workspace": "/work",
        "lease_path": "/lease",
        "target_file": str(target),
        "token_file": str(tmp_path / "execution.token"),
    }
    target.write_text(json.dumps(config))
    return target


def _bash(target):
    """One Bash call: its permission check in the MCP process, then its start
    in a process of its own that reads the target file, as `shell_prefix`
    does."""
    checked = RemoteClient(json.loads(target.read_text())).permission(
        {"tool": "Bash", "id": "toolu_1"}, {"command": "true"}
    )
    assert checked == {"value": {}}
    RemoteClient(json.loads(target.read_text())).call("control", dict(START))


def test_a_bash_call_asks_for_its_machine_once(tmp_path, monkeypatch):
    room = Room(monkeypatch)
    _bash(_session(tmp_path))
    assert room.asked == ["lease", "tool_hooks", "start"]


def test_a_start_on_that_admission_runs_where_the_machine_holds_the_project(
    tmp_path, monkeypatch
):
    """A session that sees the project at another path than the executor's
    (its owner's own computer) starts the command in the executor's copy of
    the directory it is in, as a start that took the hands itself does."""
    room = Room(monkeypatch)
    target = _session(tmp_path)
    config = json.loads(target.read_text())
    target.write_text(json.dumps({**config, "virtual_workspace": "/seen"}))
    RemoteClient(json.loads(target.read_text())).permission(
        {"tool": "Bash", "id": "toolu_1"}, {"command": "true"}
    )
    RemoteClient(json.loads(target.read_text())).call(
        "control", {**START, "cwd": "/seen/src"}
    )
    assert room.asked == ["lease", "tool_hooks", "start"]
    assert room.cwd == "/work/src"


def test_a_start_refused_on_that_admission_takes_the_machine_again(
    tmp_path, monkeypatch
):
    room = Room(monkeypatch, refuse_starts=1)
    _bash(_session(tmp_path))
    assert room.asked == ["lease", "tool_hooks", "start", "lease", "start"]


def test_a_start_long_after_the_last_admission_asks_for_the_machine(
    tmp_path, monkeypatch
):
    room = Room(monkeypatch)
    target = _session(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(executor_transport.time, "time", lambda: clock[0])
    RemoteClient(json.loads(target.read_text())).permission(
        {"tool": "Bash", "id": "toolu_1"}, {"command": "true"}
    )
    clock[0] += 60
    RemoteClient(json.loads(target.read_text())).call("control", dict(START))
    assert room.asked == ["lease", "tool_hooks", "lease", "start"]
