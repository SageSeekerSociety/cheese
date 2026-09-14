"""Reuse the central MCP process for prompt-time context synchronization."""

import hashlib
import os
import socket
from contextlib import contextmanager


def address(target):
    digest = hashlib.sha256(os.path.abspath(target).encode()).hexdigest()[:32]
    return f"/tmp/cheese-context-{os.getuid()}-{digest}.sock"


def call(target, *, return_value=False):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(60)
        try:
            connection.connect(address(target))
        except (FileNotFoundError, ConnectionRefusedError):
            return False
        connection.sendall(b"context\n")
        with connection.makefile("rb") as response:
            raw = response.readline()
        # The resident service emits this exact success response on every turn.
        import json

        result = json.loads(raw)
        if "error" in result:
            raise RuntimeError(result["error"])
        if result.get("ok") is not True or set(result) - {"ok", "value"}:
            raise RuntimeError("Invalid context synchronization response")
        return result.get("value") if return_value else True


@contextmanager
def serve(target, synchronize):
    import json
    import socketserver
    import threading

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(60)
            if self.rfile.readline(32) != b"context\n":
                return
            try:
                result = {"ok": True, "value": synchronize()}
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


if __name__ == "__main__":
    import sys

    if not call(sys.argv[1]):
        from client import sync_context

        sync_context(sys.argv[1])
