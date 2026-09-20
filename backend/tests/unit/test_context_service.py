import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.remote_execution import context_service
from app.domain.agent.harness.claude_code.remote_execution.context_service import (
    address,
    call,
    serve,
)


def test_context_service_reads_current_state_and_cleans_up(tmp_path):
    target = tmp_path / "execution.json"
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.write_text("first")
    assert call(target) is False
    with serve(target, lambda: output.write_text(source.read_text())):
        assert os.stat(address(target)).st_mode & 0o777 == 0o600
        assert call(target)
        assert output.read_text() == "first"
        source.write_text("second")
        assert call(target)
        assert output.read_text() == "second"
    assert not os.path.exists(address(target))
    assert call(target) is False


def test_context_failure_reaches_caller_and_service_survives(tmp_path):
    target = tmp_path / "execution.json"
    attempts = []

    def synchronize():
        attempts.append(True)
        if len(attempts) == 1:
            raise ValueError("context unavailable")

    with serve(target, synchronize):
        with pytest.raises(RuntimeError, match="context unavailable"):
            call(target)
        assert call(target)
    assert len(attempts) == 2


def test_distinct_sessions_do_not_share_context_service(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    calls = []
    with serve(left, lambda: calls.append("left")):
        assert call(right) is False
        with serve(right, lambda: calls.append("right")):
            assert call(right)
            assert call(left)
    assert calls == ["right", "left"]


def test_context_entry_reports_service_failure_without_direct_retry(tmp_path):
    target = tmp_path / "execution with spaces.json"
    attempts = []

    def synchronize():
        attempts.append(True)
        raise ValueError("context unavailable")

    with serve(target, synchronize):
        result = subprocess.run(
            [sys.executable, str(Path(context_service.__file__)), str(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert result.returncode != 0
    assert "context unavailable" in result.stderr
    assert attempts == [True]


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (b'{"ok": true}\n', None),
        (b'{ "ok" : true }\n', None),
        (b'{"error": "sync denied"}\n', "sync denied"),
        (b'{"ok": false}\n', "Invalid context synchronization response"),
        (b'{"ok": true, "extra": 1}\n', "Invalid context synchronization response"),
        (b"not-json\n", "JSONDecodeError"),
        (b"", "JSONDecodeError"),
    ],
)
def test_context_entry_response_compatibility(tmp_path, response, error):
    target = tmp_path / "execution.json"
    requests = []
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(address(target))
        server.listen(1)
        server.settimeout(10)

        def respond():
            with server.accept()[0] as connection:
                connection.settimeout(10)
                with connection.makefile("rb") as incoming:
                    requests.append(incoming.readline())
                connection.sendall(response)

        worker = threading.Thread(target=respond)
        worker.start()
        try:
            result = subprocess.run(
                [sys.executable, str(Path(context_service.__file__)), str(target)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            worker.join(timeout=10)
        assert not worker.is_alive()
    assert requests == [b"context\n"]
    if error is None:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert error in result.stderr
