import hashlib
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "sandbox/cheese"
WORKER = ROOT / "app/domain/agent/harness/claude_code/remote_execution/cli_worker.py"


@pytest.fixture
def worker(tmp_path):
    source = tmp_path / "cheese"
    source.write_bytes(CLI.read_bytes())
    # macOS limits Unix socket paths to 104 bytes; pytest's temp root is longer.
    address = (
        "/tmp/cheese-cli-test-"
        + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:24]
    )
    process = subprocess.Popen(
        [sys.executable, str(WORKER), address, str(source)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert select.select([process.stdout], [], [], 10)[0]
    assert process.stdout.readline().strip() == "ready", process.stderr.read()
    yield source, address, process
    if not process.stdin.closed:
        process.stdin.close()
    process.wait(timeout=10)
    assert process.returncode == 0, process.stderr.read()


def command(address, *args):
    return [sys.executable, str(CLI), *args], {
        **os.environ,
        "CHEESE_CLI_SOCKET": address,
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


def test_worker_publishes_with_current_credentials(worker):
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
            body = b'{"data":{"id":"reply"}}'
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
                },
                input="current message",
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {"id": "reply"}
        assert [row[1] for row in received] == [
            "first-test-token",
            "rotated-test-token",
        ]
        assert all(row[0] == "/topics/room/messages" for row in received)
        assert all(row[2]["content"] == "current message" for row in received)
        assert received[0][2]["request_id"] != received[1][2]["request_id"]
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
