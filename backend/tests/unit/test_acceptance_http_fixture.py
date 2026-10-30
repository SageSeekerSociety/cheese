"""HTTP fixture failures must invalidate acceptance, even off the main thread."""

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import uuid
from http.client import HTTPConnection, RemoteDisconnected
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts/remote_execution" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("malformed", [False, True])
def test_request_failure_invalidates_acceptance(tmp_path, malformed):
    model = load("model_fixture")
    server = model.Server(("127.0.0.1", 0), model.Handler)
    server.state = {"dir": tmp_path}
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(
            "POST",
            "/topics/other",
            b"\xff" if malformed else json.dumps({"title": "Updated title"}),
            {"Content-Type": "application/json"},
        )
        if malformed:
            with pytest.raises(RemoteDisconnected):
                connection.getresponse()
            with pytest.raises(AssertionError, match="UnicodeDecodeError"):
                server.assert_healthy()
            assert (tmp_path / "handler-errors.jsonl").is_file()
        else:
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read()) == {}
            server.assert_healthy()
    finally:
        connection.close()
        server.shutdown()
        if malformed:
            with pytest.raises(AssertionError, match="UnicodeDecodeError"):
                server.server_close()
        else:
            server.server_close()
        thread.join()


def test_late_handler_failure_invalidates_server_close(tmp_path):
    model = load("model_fixture")
    entered, finish = threading.Event(), threading.Event()

    class LateFailure(model.Handler):
        def do_GET(self):
            entered.set()
            assert finish.wait(3)
            raise ValueError("late fixture failure")

    server = model.Server(("127.0.0.1", 0), LateFailure)
    server.state = {"dir": tmp_path}
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request("GET", "/late")
        assert entered.wait(3)
        server.assert_healthy()
        server.shutdown()
        finish.set()
        with pytest.raises(RemoteDisconnected):
            connection.getresponse()
        with pytest.raises(AssertionError, match="late fixture failure"):
            server.server_close()
    finally:
        finish.set()
        connection.close()
        server.shutdown()
        thread.join()


def test_execution_endpoint_reaches_wire(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/remote_execution"))
    acceptance = load("acceptance")
    calls = []

    class Wire:
        async def call_executor(self, device, state, method, params):
            calls.append((device, state, method, params))
            return {"workspace": "/remote/work"}

    server = acceptance.Server(("127.0.0.1", 0), acceptance.execution_handler())
    server.state = {
        "dir": tmp_path,
        "wire": Wire(),
        "executor_state": "/recorded/state",
    }
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(
            "POST",
            "/execution",
            json.dumps({"method": "ping", "params": {}}),
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {'fixture-place-token'}",
            },
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"workspace": "/remote/work"}
        assert calls == [("acceptance-machine", "/recorded/state", "ping", {})]
        server.assert_healthy()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_a_client_that_hangs_up_before_the_answer_leaves_the_fixture_healthy(
    tmp_path, monkeypatch
):
    # The build stops a shell prefix while its executor call is in flight: the
    # prefix is gone by the time the answer is written. That is the client's
    # doing, not a fixture failure.
    monkeypatch.syspath_prepend(str(ROOT / "scripts/remote_execution"))
    acceptance = load("acceptance")
    asked, gone = threading.Event(), threading.Event()

    class Wire:
        async def call_executor(self, device, state, method, params):
            asked.set()
            assert gone.wait(3)
            return {"stdout": "x" * 4_000_000}

    server = acceptance.Server(("127.0.0.1", 0), acceptance.execution_handler())
    server.state = {"dir": tmp_path, "wire": Wire(), "executor_state": "/state"}
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    client = socket.create_connection(("127.0.0.1", server.server_port), timeout=3)
    try:
        body = json.dumps({"method": "invoke", "params": {}}).encode()
        client.sendall(
            b"POST /execution HTTP/1.1\r\nHost: fixture\r\n"
            b"Authorization: Bearer fixture-place-token\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        assert asked.wait(3)
        # Closed with a reset, as a killed process's socket is.
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()
        gone.set()
    finally:
        gone.set()
        client.close()
        server.shutdown()
        server.server_close()
        thread.join()
    assert not (tmp_path / "handler-errors.jsonl").exists()


def test_room_snapshot_upload_is_recoverable_and_idempotent(tmp_path):
    model = load("model_fixture")
    room_class = load("room_fixture").RoomExecutor
    room = object.__new__(room_class)
    room.project, room.task = "project", "task"
    room.owner = tmp_path
    room.snapshots = {}
    source = tmp_path / "source"
    source.mkdir()

    def git(directory, *args):
        return subprocess.check_output(
            ["git", "-C", str(directory), *args], text=True, stderr=subprocess.PIPE
        ).strip()

    git(source, "init", "-q")
    (source / "draft.md").write_text("Recovered task work\n")
    git(source, "add", "draft.md")
    git(
        source,
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-qm",
        "Task snapshot",
    )
    snapshot_sha = git(source, "rev-parse", "HEAD")
    ref = "refs/cheese/snapshots/task"
    git(source, "update-ref", ref, snapshot_sha)
    bundle = tmp_path / "upload.bundle"
    git(source, "bundle", "create", str(bundle), ref)
    content = bundle.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    server = model.Server(("127.0.0.1", 0), room.handler(model.Handler))
    server.state = {"dir": tmp_path}
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        receipts = []
        for _ in range(2):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            try:
                connection.request(
                    "PUT",
                    f"/projects/project/git/tasks/task/snapshots/{snapshot_sha}",
                    content,
                    {
                        "Content-Type": "application/x-git-bundle",
                        "Authorization": f"Bearer {'room-handed-token'}",
                        "X-Content-SHA256": digest,
                        "X-Cheese-Head": snapshot_sha,
                    },
                )
                response = connection.getresponse()
                assert response.status == 200
                receipts.append(json.loads(response.read())["data"])
            finally:
                connection.close()
        assert receipts[0] == receipts[1]
        assert receipts[0]["snapshot_sha"] == snapshot_sha
        assert receipts[0]["digest"] == digest
        assert len(room.snapshots) == 1
        recovered = tmp_path / "recovered"
        recovered.mkdir()
        git(recovered, "init", "-q")
        git(recovered, "fetch", room.snapshots[digest]["bundle"], ref)
        assert git(recovered, "show", "FETCH_HEAD:draft.md") == "Recovered task work"
        server.assert_healthy()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class _OwnerRelay(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """The connector's platform socket and the owner behind it: each request
    goes on to ``origin`` under ``/machine``, with what the owner adds."""

    daemon_threads = True

    def __init__(self, path, origin, device_id, secret):
        self.origin, self.device_id, self.secret = origin, device_id, secret
        super().__init__(str(path), _Relay)


class _Relay(socketserver.StreamRequestHandler):
    def handle(self):
        method, target, version = self.rfile.readline().decode().split()
        upstream = socket.create_connection(self.server.origin)
        upstream.sendall(
            f"{method} /machine{target} {version}\r\n"
            f"x-machine-device: {self.server.device_id}\r\n"
            "x-machine-state: room\r\n"
            f"x-device-connection-secret: {self.server.secret}\r\n".encode()
        )

        def up():
            try:
                while chunk := self.rfile.read1(65536):
                    upstream.sendall(chunk)
            except OSError:  # the answer is in and the client has hung up
                pass

        threading.Thread(target=up, daemon=True).start()
        with upstream:
            while chunk := upstream.recv(65536):
                self.connection.sendall(chunk)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix platform socket")
def test_room_cli_worktree_and_sync_use_platform_socket_without_room_token(
    tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/remote_execution"))
    model = load("model_fixture")
    room_module = load("room_fixture")
    room = object.__new__(room_module.RoomExecutor)
    room.project = str(uuid.uuid4())
    room.task = str(uuid.uuid4())
    resource = str(uuid.uuid4())
    room.target = {"resource_id": resource, "device_id": "acceptance-machine"}
    room.owner = tmp_path
    room.snapshots = {}
    origin = tmp_path / "task-origin"
    origin.mkdir()
    (origin / "README.md").write_text("seed\n")
    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "."],
        [
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "seed",
        ],
        ["branch", f"task/{room.task}"],
    ):
        subprocess.run(["git", "-C", str(origin), *args], check=True)
    room.remote = str(origin)
    socket_dir = Path(tempfile.mkdtemp(dir="/tmp", prefix="cheese-room-sock-"))
    socket_path = socket_dir / "platform.sock"
    handler = room.handler(model.Handler)
    server = model.Server(("127.0.0.1", 0), handler)
    server.state = {"dir": tmp_path}
    socket_server = _OwnerRelay(
        socket_path,
        ("127.0.0.1", server.server_port),
        "acceptance-machine",
        load("owner_fixture").SECRET,
    )
    threads = [
        threading.Thread(target=server.serve_forever),
        threading.Thread(target=socket_server.serve_forever),
    ]
    for thread in threads:
        thread.start()
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "CHEESE_API": f"http://127.0.0.1:{server.server_port}",
        "CHEESE_TOKEN": "",
        "CHEESE_PLATFORM_SOCKET": str(socket_path),
        "CHEESE_PROJECT": room.project,
        "CHEESE_TOPIC": resource,
        "GIT_AUTHOR_NAME": "fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.test",
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.test",
    }
    cli = str(ROOT / "backend/sandbox/cheese")
    try:
        worktree = Path(
            subprocess.check_output(
                [sys.executable, cli, "worktree", room.task],
                env=env,
                text=True,
                timeout=30,
            ).strip()
        )
        (worktree / "draft.md").write_text("Recovered task work\n")
        for _ in range(2):
            subprocess.run(
                [sys.executable, cli, "sync", "--all"],
                env=env,
                check=True,
                timeout=30,
            )
        assert len(room.snapshots) == 1
        server.assert_healthy()
    finally:
        socket_server.shutdown()
        socket_server.server_close()
        server.shutdown()
        server.server_close()
        for thread in threads:
            thread.join()
        shutil.rmtree(socket_dir, ignore_errors=True)
