"""Reuse the central MCP process for prompt-time context synchronization."""

import hashlib
import json
import os
import socket
from contextlib import contextmanager


def address(target):
    digest = hashlib.sha256(os.path.abspath(target).encode()).hexdigest()[:32]
    return f"/tmp/cheese-context-{os.getuid()}-{digest}.sock"


def call(target):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(60)
        try:
            connection.connect(address(target))
        except (FileNotFoundError, ConnectionRefusedError):
            return False
        connection.sendall(b"context\n")
        with connection.makefile("rb") as response:
            result = json.loads(response.readline())
        if "error" in result:
            raise RuntimeError(result["error"])
        if result != {"ok": True}:
            raise RuntimeError("Invalid context synchronization response")
        return True


@contextmanager
def serve(target, synchronize):
    import socketserver
    import threading

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(60)
            if self.rfile.readline(32) != b"context\n":
                return
            try:
                synchronize()
                result = {"ok": True}
            except Exception as exc:
                result = {"error": str(exc)}
            self.wfile.write(json.dumps(result).encode() + b"\n")

    path = address(target)
    if os.path.exists(path):
        with socket.socket(socket.AF_UNIX) as existing:
            try:
                existing.connect(path)
            except ConnectionRefusedError:
                os.unlink(path)
            else:
                raise RuntimeError("Context synchronization service already running")
    with socketserver.UnixStreamServer(path, Handler) as server:
        os.chmod(path, 0o600)
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        thread.start()
        try:
            yield
        finally:
            server.shutdown()
            thread.join()
            os.unlink(path)
