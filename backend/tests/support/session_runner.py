"""A stand-in for a Claude Code session's runner, at the socket it hands the
session (`CHEESE_SESSION_SOCKET`).

A session's own helper (the remote-execution transport) asks its runner which
run a platform call is made in (`claude_code.runner.Runner.dispatch` ``run``)
before every platform tool call, so a test that drives the transport without a
real runner gives it this one. It answers ``run`` with the run it is told and
refuses anything else.
"""

import json
import shutil
import socket
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def standing_runner(run: str) -> Iterator[str]:
    """Listen as the session's runner naming ``run``; yields the socket path."""
    # Under /tmp, whatever TMPDIR says: a socket path is capped near 104 bytes
    # on macOS (the convention `test_device_sandbox_macos.py` follows too).
    directory = Path(tempfile.mkdtemp(prefix="cxr", dir="/tmp"))
    path = directory / "r.sock"
    listening = socket.socket(socket.AF_UNIX)
    listening.bind(str(path))
    listening.listen()

    def answer() -> None:
        while True:
            try:
                connection, _ = listening.accept()
            except OSError:
                return
            with connection:
                request = json.loads(connection.makefile("rb").readline())
                reply = (
                    {"result": {"run": run}}
                    if request["method"] == "run"
                    else {"error": f"unexpected {request['method']}"}
                )
                connection.sendall(json.dumps(reply).encode() + b"\n")

    threading.Thread(target=answer, daemon=True).start()
    try:
        yield str(path)
    finally:
        listening.close()
        shutil.rmtree(directory, ignore_errors=True)
