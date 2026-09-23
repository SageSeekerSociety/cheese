"""HTTP fixture failures must invalidate acceptance, even off the main thread."""

import importlib.util
import json
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
