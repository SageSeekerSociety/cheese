import array
import hashlib
import json
import os
import select
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "sandbox/cheese"
WORKER = ROOT / "app/domain/agent/cli_worker.py"
CLIENT = ROOT / "app/domain/agent/harness/claude_code/remote_execution/cli_client.py"


@pytest.fixture(params=["pipe", "file"])
def worker(tmp_path, request):
    source = tmp_path / "cheese"
    source.write_bytes(CLI.read_bytes())
    # macOS limits Unix socket paths to 104 bytes; pytest's temp root is longer.
    address = (
        "/tmp/cheese-cli-test-"
        + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:24]
    )
    errors = (tmp_path / "worker.log").open("w+") if request.param == "file" else None
    process = subprocess.Popen(
        [sys.executable, str(WORKER), address, str(source)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=errors if errors is not None else subprocess.PIPE,
        text=True,
    )
    assert select.select([process.stdout], [], [], 10)[0]
    assert process.stdout.readline().strip() == "ready", process.stderr.read()
    yield source, address, process
    if not process.stdin.closed:
        process.stdin.close()
    process.wait(timeout=10)
    assert process.returncode == 0, process.stderr.read()
    if errors is not None:
        errors.close()


def command(address, *args):
    return [sys.executable, str(CLIENT), *args], {
        **os.environ,
        "CHEESE_CLI_SOCKET": address,
    }


def mcp_call(address, tmp_path, request):
    paths = [tmp_path / name for name in ("stdin", "stdout", "stderr")]
    paths[0].write_text("")
    with (
        paths[0].open("rb") as stdin,
        paths[1].open("wb") as stdout,
        paths[2].open("wb") as stderr,
        socket.socket(socket.AF_UNIX) as connection,
    ):
        connection.connect(address)
        connection.sendmsg(
            [b"\0"],
            [
                (
                    socket.SOL_SOCKET,
                    socket.SCM_RIGHTS,
                    array.array(
                        "i", [stdin.fileno(), stdout.fileno(), stderr.fileno()]
                    ),
                )
            ],
        )
        connection.sendall(
            json.dumps(
                {
                    "mcp": request,
                    "cwd": str(tmp_path),
                    "env": dict(os.environ),
                    "stdio": [
                        {"encoding": "utf-8", "errors": "strict"} for _ in range(3)
                    ],
                }
            ).encode()
            + b"\n"
        )
        receipt = json.loads(connection.makefile("rb").readline())
    return receipt, paths[1].read_text(), paths[2].read_text()


def test_worker_discovers_every_leaf_as_a_structured_tool(worker, tmp_path):
    receipt, _, _ = mcp_call(worker[1], tmp_path, {"method": "tools/list"})
    tools = {tool["name"]: tool for tool in receipt["result"]["tools"]}
    assert set(tools) == {
        "cheese_accept_request",
        "cheese_api",
        "cheese_artifact",
        "cheese_ask",
        "cheese_bind",
        "cheese_chat_get",
        "cheese_chat_list",
        "cheese_chat_replies",
        "cheese_chat_search",
        "cheese_chat_send",
        "cheese_close_task",
        "cheese_convert",
        "cheese_decision",
        "cheese_describe",
        "cheese_doc_get",
        "cheese_doc_set",
        "cheese_feedback_propose",
        "cheese_fetch",
        "cheese_gh_token",
        "cheese_library_get",
        "cheese_library_ls",
        "cheese_lock",
        "cheese_members",
        "cheese_milestone",
        "cheese_notify",
        "cheese_push_fix",
        "cheese_ready",
        "cheese_recalc",
        "cheese_recall",
        "cheese_remember",
        "cheese_serve",
        "cheese_split",
        "cheese_status",
        "cheese_sync",
        "cheese_tell",
        "cheese_title",
        "cheese_unlock",
        "cheese_worktree",
    }
    assert tools["cheese_split"]["inputSchema"]["required"] == ["title"]
    assert tools["cheese_split"]["inputSchema"]["properties"]["contributor"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "实际贡献者的 handle，可重复",
        "default": [],
    }
    assert tools["cheese_serve"]["inputSchema"]["properties"]["port"]["type"] == (
        "integer"
    )


def test_worker_translates_values_to_argv_without_shell_interpretation(
    worker, tmp_path
):
    source = worker[0]
    source.write_text(
        "import argparse, json, pathlib, sys\n"
        "def build_parser():\n"
        " p=argparse.ArgumentParser(); s=p.add_subparsers(dest='cmd', required=True)\n"
        " q=s.add_parser('write'); q.add_argument('path')\n"
        " q.add_argument('--tag', action='append'); return p\n"
        "if __name__ == '__main__':\n"
        " a=build_parser().parse_args()\n"
        " pathlib.Path(a.path).write_text(json.dumps(a.tag))\n"
    )
    target = tmp_path / "literal;$(touch escaped)"
    receipt, _, stderr = mcp_call(
        worker[1],
        tmp_path,
        {
            "method": "tools/call",
            "tool": "cheese_write",
            "arguments": {"path": str(target), "tag": ["one", "two"]},
        },
    )
    assert receipt["status"] == 0, stderr
    assert json.loads(target.read_text()) == ["one", "two"]
    assert not (tmp_path / "escaped").exists()


def test_worker_preserves_dash_leading_structured_strings(worker, tmp_path):
    source = worker[0]
    source.write_text(
        "import argparse, json\n"
        "def build_parser():\n"
        " p=argparse.ArgumentParser(); s=p.add_subparsers(dest='cmd', required=True)\n"
        " q=s.add_parser('capture'); q.add_argument('text')\n"
        " q.add_argument('--title'); q.add_argument('--tag', action='append')\n"
        " return p\n"
        "if __name__ == '__main__':\n"
        " a=build_parser().parse_args(); print(json.dumps(vars(a)))\n"
    )
    receipt, stdout, stderr = mcp_call(
        worker[1],
        tmp_path,
        {
            "method": "tools/call",
            "tool": "cheese_capture",
            "arguments": {
                "text": "--help",
                "title": "--task",
                "tag": ["--title=changed"],
            },
        },
    )
    assert receipt["status"] == 0, stderr
    assert json.loads(stdout) == {
        "cmd": "capture",
        "text": "--help",
        "title": "--task",
        "tag": ["--title=changed"],
    }


def test_worker_preserves_actual_cli_help(worker):
    _, address, _ = worker
    args, env = command(address, "chat", "send", "--help")
    actual = subprocess.run(args, env=env, capture_output=True, timeout=10)
    expected = subprocess.run(
        [sys.executable, str(CLI), "chat", "send", "--help"],
        capture_output=True,
        timeout=10,
    )
    assert (actual.returncode, actual.stdout, actual.stderr) == (
        expected.returncode,
        expected.stdout,
        expected.stderr,
    )


@pytest.mark.parametrize("encoding", ["utf-8", "latin-1"])
def test_worker_publishes_with_current_credentials(worker, encoding):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = []

    class API(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers["X-Cheese-Token"],
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            body = '{"data":{"id":"réponse"}}'.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        for token in ("first-test-token", "rotated-test-token"):
            args, env = command(worker[1], "chat", "send", "--file", "-")
            result = subprocess.run(
                args,
                env={
                    **env,
                    "CHEESE_TOKEN": token,
                    "CHEESE_TOPIC": "room",
                    "CHEESE_API": f"http://127.0.0.1:{server.server_port}",
                    "NO_PROXY": "*",
                    "PYTHONIOENCODING": encoding,
                },
                input="café".encode(encoding),
                capture_output=True,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout.decode(encoding)) == {"id": "réponse"}
        assert [row[1] for row in received] == [
            "first-test-token",
            "rotated-test-token",
        ]
        assert all(row[0] == "/topics/room/messages" for row in received)
        assert all(row[2]["content"] == "café" for row in received)
        assert received[0][2]["request_id"] != received[1][2]["request_id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_cli_client_publishes_inline_chat_without_worker(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = []

    class API(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.path,
                    self.headers["X-Cheese-Token"],
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            body = b'{"data":{"id":"direct"}}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        args, env = command(
            "/tmp/socket-that-does-not-exist", "chat", "send", "direct message"
        )
        result = subprocess.run(
            args,
            env={
                **env,
                "CHEESE_TOKEN": "direct-token",
                "CHEESE_TOPIC": "room",
                "CHEESE_API": f"http://127.0.0.1:{server.server_port}",
                "NO_PROXY": "*",
            },
            capture_output=True,
            timeout=10,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"id": "direct"}
        assert received[0][0] == "/topics/room/messages"
        assert received[0][1] == "direct-token"
        assert received[0][2]["content"] == "direct message"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_worker_refreshes_source_and_isolates_concurrent_credentials(worker, tmp_path):
    source, address, _ = worker
    source.write_text(
        "import json, os, sys, time\n"
        "TOKEN = os.environ.get('CHEESE_TOKEN')\n"
        "if __name__ == '__main__':\n"
        "    time.sleep(0.1)\n"
        "    print(json.dumps([TOKEN, os.getcwd(), sys.stdin.read()]))\n"
    )
    children = []
    for name in ("first", "second"):
        cwd = tmp_path / name
        cwd.mkdir()
        args, env = command(address, "probe")
        children.append(
            (
                name,
                cwd,
                subprocess.Popen(
                    args,
                    env={**env, "CHEESE_TOKEN": name},
                    cwd=cwd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ),
            )
        )
    for name, cwd, child in children:
        stdout, stderr = child.communicate(name + " input", timeout=10)
        assert child.returncode == 0 and not stderr
        assert json.loads(stdout) == [name, str(cwd), name + " input"]
    source.write_text("if __name__ == '__main__':\n    raise SystemExit(7)\n")
    args, env = command(address, "probe")
    assert (
        subprocess.run(args, env=env, capture_output=True, timeout=10).returncode == 7
    )


@pytest.mark.parametrize("stop_owner", [False, True])
def test_worker_cancels_after_disconnect(worker, tmp_path, stop_owner):
    source, address, _ = worker
    marker = tmp_path / "completion"
    source.write_text(
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "if __name__ == '__main__':\n"
        "    print(os.getpid(), flush=True)\n"
        "    time.sleep(2)\n"
        "    Path(sys.argv[1]).write_text('completed')\n"
    )
    args, env = command(address, str(marker))
    child = subprocess.Popen(
        args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert select.select([child.stdout], [], [], 10)[0]
    invocation_pid = int(child.stdout.readline())
    if stop_owner:
        worker[2].stdin.close()
    else:
        child.kill()
    child.wait(timeout=5)
    deadline = time.monotonic() + 1
    while True:
        try:
            os.kill(invocation_pid, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "Cancelled CLI child is still alive"
        time.sleep(0.02)
    assert not marker.exists()
