"""Reuse the central MCP process for prompt-time context synchronization."""

import hashlib
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
            raw = response.readline()
        # The resident service emits this exact success response on every turn.
        if raw == b'{"ok": true}\n':
            return True
        import json

        result = json.loads(raw)
        if "error" in result:
            raise RuntimeError(result["error"])
        if result != {"ok": True}:
            raise RuntimeError("Invalid context synchronization response")
        return True


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
                synchronize()
                result = {"ok": True}
            except Exception as exc:
                result = {"error": str(exc)}
            self.wfile.write(json.dumps(result).encode() + b"\n")

    # The address belongs to the newest service. Two can overlap in one home:
    # a relaunched screen's `claude` started while the old one was still
    # running (2026-09-20), and a reconnect starts the new server as soon as
    # the old one's wrapper shell exits, not when its Python has finished. An
    # older service keeping the address made the newer one exit at startup, and
    # the room lost its platform tools. Either service synchronizes the same
    # target file, so which one answers a prompt does not matter.
    path = address(target)
    with _address_lock(path):
        staged = f"{path}.{os.getpid()}"
        if os.path.exists(staged):
            os.unlink(staged)
        server = socketserver.UnixStreamServer(staged, Handler)
        os.chmod(staged, 0o600)
        os.replace(staged, path)
        identity = os.stat(path).st_ino
    with server:
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
            with _address_lock(path):
                # Only its own: a newer service may hold the address by now.
                if os.path.exists(path) and os.stat(path).st_ino == identity:
                    os.unlink(path)


@contextmanager
def _address_lock(path):
    """Serialize taking and releasing one address. Held on the directory rather
    than on a lock file of its own, which would be left behind for every room."""
    import fcntl

    descriptor = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    import sys

    if not call(sys.argv[1]):
        from client import sync_context

        sync_context(sys.argv[1])
