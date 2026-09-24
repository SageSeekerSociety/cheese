"""Exercise actual loopback listeners without contacting a model provider."""

import importlib.util
from pathlib import Path
import socket
import threading
import unittest

spec = importlib.util.spec_from_file_location(
    "metering_health",
    Path(__file__).resolve().parents[1] / "metering-proxy/healthcheck.py",
)
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class MeteringHealthTest(unittest.TestCase):
    def probe(self, response):
        with socket.socket() as reverse, socket.socket() as connect:
            reverse.bind(("127.0.0.1", 0))
            connect.bind(("127.0.0.1", 0))
            reverse.listen()
            connect.listen()
            requests = []

            def serve():
                with connect.accept()[0] as client:
                    requests.append(client.recv(4096))
                    client.sendall(response)

            worker = threading.Thread(target=serve)
            worker.start()
            try:
                health.check(reverse.getsockname()[1], connect.getsockname()[1])
            finally:
                worker.join(timeout=3)
            return requests

    def test_healthy_proxy_rejects_unattributed_connect(self):
        requests = self.probe(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
        self.assertEqual(len(requests), 1)
        self.assertIn(b"CONNECT api.anthropic.com:443", requests[0])
        self.assertNotIn(b"Authorization:", requests[0])

    def test_running_but_unprotected_proxy_is_unhealthy(self):
        with self.assertRaises(RuntimeError):
            self.probe(b"HTTP/1.1 200 Connection established\r\n\r\n")

    def test_listener_returning_errors_is_unhealthy(self):
        with self.assertRaises(RuntimeError):
            self.probe(b"HTTP/1.1 503 Service Unavailable\r\n\r\n")

    def test_missing_reverse_listener_is_unhealthy(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with self.assertRaises(OSError):
            health.check(port, port)


if __name__ == "__main__":
    unittest.main()
