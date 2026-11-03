"""platform_loopback.py: a URL for a program that cannot dial a Unix socket.

What it must keep true: a request with the run's prefix reaches the platform
socket with the prefix taken off and the answer comes back whole; a request
without the prefix, from any other program on the machine, never reaches the
socket.
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("platform_loopback.py")


class PlatformLoopback(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.socket_path = os.path.join(self.dir, "platform.sock")
        self.heard = []
        server = socket.socket(socket.AF_UNIX)
        server.bind(self.socket_path)
        server.listen()
        self.addCleanup(server.close)

        def answer():
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                with conn:
                    data = b""
                    while b"\r\n\r\n" not in data:
                        data += conn.recv(65536)
                    self.heard.append(data)
                    conn.sendall(b"HTTP/1.1 200 OK\r\nconnection: close\r\n\r\n\xe7\xad\x94")

        threading.Thread(target=answer, daemon=True).start()
        self.helper = subprocess.Popen(
            [sys.executable, str(SCRIPT)],
            env={
                **os.environ,
                "CHEESE_PLATFORM_SOCKET": self.socket_path,
                "PLATFORM_LOOPBACK_HEAD_TIMEOUT_S": "1",
            },
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.helper.kill)
        self.base = self.helper.stdout.readline().strip()

    def _get(self, path):
        host, port = self.base.split("//")[1].split("/")[0].split(":")
        with socket.create_connection((host, int(port))) as conn:
            conn.sendall(f"GET {path} HTTP/1.1\r\nhost: x\r\n\r\n".encode())
            answer = b""
            while chunk := conn.recv(65536):
                answer += chunk
        return answer

    def test_a_request_with_the_prefix_reaches_the_socket_without_it(self):
        prefix = "/" + self.base.split("/", 3)[3]
        answer = self._get(prefix + "/llm/v1/models")
        self.assertTrue(answer.startswith(b"HTTP/1.1 200"), answer)
        self.assertTrue(answer.endswith("答".encode()))
        self.assertEqual(self.heard[0].split(b"\r\n")[0], b"GET /llm/v1/models HTTP/1.1")

    def test_a_request_without_the_prefix_never_reaches_the_socket(self):
        prefix = "/" + self.base.split("/", 3)[3]
        for path in ("/llm/v1/models", "/guess/llm/v1/models", "/", prefix + "/whoami"):
            self.assertTrue(self._get(path).startswith(b"HTTP/1.1 404"), path)
        self.assertEqual(self.heard, [])

    def test_a_connection_that_never_sends_its_request_is_dropped(self):
        host, port = self.base.split("//")[1].split("/")[0].split(":")
        with socket.create_connection((host, int(port))) as idle:
            idle.settimeout(30)
            self.assertEqual(idle.recv(1), b"")


if __name__ == "__main__":
    unittest.main()
