"""A command on a machine runs under the caller of the call that started it.

The rules, as a person would state them:

- a command started for one session sees that session's name, conversation
  and seat;
- a later command for another session on the same machine sees the other
  one's, never the first's;
- a command still running keeps the name it was started with;
- a request a command makes to the platform names the session it was started
  for.

The executor is the shipped runtime, run as a process and asked over its
socket; the platform socket is a loopback fixture.
"""

import base64
import json
import os
import shlex
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.remote_execution import runtime

RUNTIME = Path(runtime.__file__)
CLI = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
NAMES = 'printf "%s|%s|%s" "$CHEESE_ATTACHMENT" "$CHEESE_TOPIC" "$CHEESE_AUTHOR"'


def _caller(session, conversation, author):
    return {"attachment": session, "conversation": conversation, "author": author}


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        request, _ = super().get_request()
        return request, ("socket", 0)


def _platform(seen):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(
                {
                    "path": self.path,
                    "attachment": self.headers.get("x-cheese-attachment"),
                }
            )
            raw = json.dumps({"data": {}}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    return Handler


@pytest.fixture
def machine(tmp_path):
    """A running executor whose environment is the platform socket's, and
    what that socket was asked."""
    seen = []
    folder = tempfile.mkdtemp(dir="/tmp")
    socket_path = os.path.join(folder, "platform.sock")
    platform = _UnixHTTPServer(socket_path, _platform(seen))
    threading.Thread(target=platform.serve_forever, daemon=True).start()
    workspace = tmp_path / "work"
    workspace.mkdir()
    state = tmp_path / "state"
    own = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CHEESE_") and "proxy" not in key.lower()
    }
    subprocess.run(
        [sys.executable, str(RUNTIME), "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(workspace),
                "env": {
                    "CHEESE_API": "http://127.0.0.1:9",
                    "CHEESE_PLATFORM_SOCKET": socket_path,
                },
            }
        ),
        env=own,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    try:
        yield state, workspace, seen
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )
        platform.shutdown()
        platform.server_close()


def _start(state, workspace, body, caller=None):
    command_id = "shell-" + uuid.uuid4().hex
    runtime.request(
        state,
        "control",
        {
            "subtype": "shell",
            "operation": "start",
            "command_id": command_id,
            "kind": "sh",
            "body": body,
            "cwd": str(workspace),
            "env": {},
            "merge": True,
            "stdin": None,
            **({"caller": caller} if caller else {}),
        },
    )
    return command_id


def _collect(state, command_id, timeout=30):
    output, deadline = b"", time.monotonic() + timeout
    while True:
        answer = runtime.request(
            state,
            "control",
            {
                "subtype": "shell",
                "operation": "read",
                "command_id": command_id,
                "out": len(output),
                "err": 0,
                "wait": 1,
            },
        )
        output += base64.b64decode(answer["out"])
        if "exit" in answer:
            return output.decode(), answer["exit"]
        assert time.monotonic() < deadline, output


def test_each_command_sees_the_caller_that_started_it(machine):
    state, workspace, _ = machine
    first = _start(state, workspace, NAMES, _caller("s-a", "t-a", "teammate-a"))
    assert _collect(state, first) == ("s-a|t-a|teammate-a", 0)

    second = _start(state, workspace, NAMES, _caller("s-b", "t-b", "teammate-b"))
    assert _collect(state, second) == ("s-b|t-b|teammate-b", 0)


def test_a_running_command_keeps_the_name_it_was_started_with(machine):
    state, workspace, _ = machine
    gate = workspace / "go"
    waiting = _start(
        state,
        workspace,
        f"while [ ! -f {shlex.quote(str(gate))} ]; do sleep 0.05; done; {NAMES}",
        _caller("s-a", "t-a", "teammate-a"),
    )
    other = _start(state, workspace, NAMES, _caller("s-b", "t-b", "teammate-b"))
    assert _collect(state, other) == ("s-b|t-b|teammate-b", 0)

    gate.touch()
    assert _collect(state, waiting) == ("s-a|t-a|teammate-a", 0)


def test_a_command_with_no_caller_still_runs_and_names_nobody(machine):
    """The environment's own operation (a probe, a person's save) needs no
    name and is not given one."""
    state, workspace, _ = machine
    _start(state, workspace, "true", _caller("s-a", "t-a", "teammate-a"))
    plain = _start(state, workspace, NAMES)
    assert _collect(state, plain) == ("||", 0)


def test_a_request_from_a_command_names_its_caller_to_the_platform(machine):
    state, workspace, seen = machine
    listing = f"{shlex.quote(sys.executable)} {shlex.quote(str(CLI))} template list"

    for session, conversation in (("s-a", "t-a"), ("s-b", "t-b")):
        command = _start(
            state, workspace, listing, _caller(session, conversation, "teammate")
        )
        assert _collect(state, command)[1] == 0

    assert seen == [
        {"path": "/api/topics/t-a/files/templates", "attachment": "s-a"},
        {"path": "/api/topics/t-b/files/templates", "attachment": "s-b"},
    ]
