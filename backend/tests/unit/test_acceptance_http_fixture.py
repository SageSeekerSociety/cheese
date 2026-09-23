"""HTTP fixture failures must invalidate acceptance, even off the main thread."""

import hashlib
import importlib.util
import json
import subprocess
import threading
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
def test_put_failure_invalidates_acceptance(tmp_path, malformed):
    model = load("model_fixture")
    rc = load("rc_fixture").RemoteControlFixture(tmp_path, lambda *a, **kw: None)
    server = model.Server(("127.0.0.1", 0), rc.handler(model.Handler))
    server.state = {"dir": tmp_path}
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request(
            "PUT",
            f"/v1/code/sessions/{rc.sid}",
            b"\xff" if malformed else json.dumps({"title": "Updated title"}),
            {"Content-Type": "application/json"},
        )
        if malformed:
            with pytest.raises(RemoteDisconnected):
                connection.getresponse()
            with pytest.raises(AssertionError, match="Cannot parse PUT"):
                server.assert_healthy()
            assert (tmp_path / "handler-errors.jsonl").is_file()
        else:
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["title"] == "Updated title"
            server.assert_healthy()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()


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
                        "X-Cheese-Token": "room-fixture-token",
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
