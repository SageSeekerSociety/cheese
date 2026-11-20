#!/usr/bin/env python3
"""A port reserved for a server is given to nobody else, and the server takes it.

The ingress suites start two nginx processes on ports picked in advance; a
number handed out twice left the second unable to bind (main Required CI run
40270213475: `bind() to 127.0.0.1:44993 failed (98: Address already in use)`).
"""

import errno
import socket
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from reserved_port import reserve_port


class Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        pass


class ReservedPortTest(unittest.TestCase):
    def test_no_other_bind_is_given_the_reserved_port(self):
        port = reserve_port()
        with socket.socket() as other:
            with self.assertRaises(OSError) as refused:
                other.bind(("127.0.0.1", port))
        self.assertEqual(refused.exception.errno, errno.EADDRINUSE)
        # More picks than the ephemeral range holds, so a number left free
        # comes round again (macOS deals them in order, Linux at random).
        for _ in range(50000):
            with socket.socket() as other:
                other.bind(("127.0.0.1", 0))
                self.assertNotEqual(other.getsockname()[1], port)

    def test_a_server_listens_on_the_reserved_port(self):
        server = ThreadingHTTPServer(("127.0.0.1", reserve_port()), Ok)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=3) as response:
                self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()

    def test_a_wildcard_listener_takes_the_reserved_port(self):
        # The box's own frontend port listens on 0.0.0.0.
        port = reserve_port()
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", port))
            listener.listen()
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                listener.accept()[0].close()


if __name__ == "__main__":
    unittest.main()
