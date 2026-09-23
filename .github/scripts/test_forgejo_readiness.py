import contextlib
import http.server
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReadinessTest(unittest.TestCase):
    @contextlib.contextmanager
    def service(self, *, recover):
        class Handler(http.server.BaseHTTPRequestHandler):
            probes = 0

            def do_GET(self):
                if self.path == "/minio/health/live":
                    Handler.probes += 1
                    if not recover or Handler.probes == 1:
                        self.connection.setsockopt(
                            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                        )
                        self.connection.close()
                        self.close_connection = True
                        return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ready")

            def log_message(self, *_args):
                pass

        with http.server.HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                yield server.server_port, Handler
            finally:
                server.shutdown()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def start(self, port):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            # Container lifecycle is outside this test; curl uses real sockets.
            docker = root / "docker"
            docker.write_text("#!/bin/sh\nexit 0\n")
            docker.chmod(0o755)
            token = root / "token"
            token.write_text("test-token")
            env = dict(
                os.environ,
                PATH=f"{root}{os.pathsep}{os.environ['PATH']}",
                FORGEJO_TEST_PORT=str(port),
                FORGEJO_TEST_S3_PORT=str(port),
                FORGEJO_TEST_TOKEN_FILE=str(token),
                GITHUB_ENV=str(root / "github-env"),
                NO_PROXY="127.0.0.1",
                no_proxy="127.0.0.1",
            )
            return subprocess.run(
                ["bash", str(ROOT / "backend/tests/forgejo/start.sh")],
                env=env,
                capture_output=True,
                text=True,
                timeout=40,
            )

    def test_connection_reset_during_startup_can_recover(self):
        with self.service(recover=True) as (port, handler):
            result = self.start(port)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(handler.probes, 2)

    def test_service_that_never_becomes_ready_fails_within_budget(self):
        with self.service(recover=False) as (port, _handler):
            started = time.monotonic()
            result = self.start(port)
            elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 35)


if __name__ == "__main__":
    unittest.main()
