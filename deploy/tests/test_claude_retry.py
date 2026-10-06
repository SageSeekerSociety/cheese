"""Run inside the pinned metering image: a real proxy and a local fake upstream."""

import http.client
import json
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, ResponseReceived, StreamEnded

PROXY_SOURCE = Path(__file__).resolve().parents[1] / "metering-proxy"


class H2Client:
    def __init__(self, host, port, timeout):
        context = ssl._create_unverified_context()
        context.set_alpn_protocols(["h2"])
        self.socket = context.wrap_socket(
            socket.create_connection((host, port), timeout), server_hostname="localhost"
        )
        self.h2 = H2Connection(
            H2Configuration(client_side=True, header_encoding="utf-8")
        )
        self.h2.initiate_connection()
        self.socket.sendall(self.h2.data_to_send())
        self.buffer = bytearray()
        self.headers = None
        self.finished = False

    def request(self, method, path, body):
        self.h2.send_headers(
            1,
            [
                (":method", method),
                (":path", path),
                (":scheme", "https"),
                (":authority", "localhost"),
            ],
        )
        self.h2.send_data(1, body, end_stream=True)
        self.socket.sendall(self.h2.data_to_send())

    def receive(self):
        data = self.socket.recv(65536)
        if not data:
            raise EOFError("HTTP/2 response ended early")
        for event in self.h2.receive_data(data):
            if isinstance(event, ResponseReceived):
                self.headers = dict(event.headers)
                self.status = int(self.headers[":status"])
            elif isinstance(event, DataReceived):
                self.buffer.extend(event.data)
                self.h2.acknowledge_received_data(
                    event.flow_controlled_length, event.stream_id
                )
            elif isinstance(event, StreamEnded):
                self.finished = True
        self.socket.sendall(self.h2.data_to_send())

    def getresponse(self):
        while self.headers is None:
            self.receive()
        return self

    def readline(self):
        while b"\n" not in self.buffer and not self.finished:
            self.receive()
        end = self.buffer.index(b"\n") + 1
        result = bytes(self.buffer[:end])
        del self.buffer[:end]
        return result

    def read(self):
        while not self.finished:
            self.receive()
        return bytes(self.buffer)

    def getheader(self, name):
        return self.headers.get(name)

    def close(self):
        self.socket.close()


class H2Upstream:
    def __init__(self, attempts, allow_finish, reject_both):
        self.attempts, self.allow_finish, self.reject_both = (
            attempts,
            allow_finish,
            reject_both,
        )
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        (root / "key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        (root / "cert").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(root / "cert", root / "key")
        self.context.set_alpn_protocols(["h2"])
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen()
        self.socket.settimeout(0.2)
        self.server_port = self.socket.getsockname()[1]
        self.stopped = threading.Event()

    def serve_forever(self):
        while not self.stopped.is_set():
            try:
                raw, _ = self.socket.accept()
            except socket.timeout:
                continue
            with self.context.wrap_socket(raw, server_side=True) as connection:
                h2 = H2Connection(
                    H2Configuration(client_side=False, header_encoding="utf-8")
                )
                h2.initiate_connection()
                connection.sendall(h2.data_to_send())
                requests = {}
                while data := connection.recv(65536):
                    for event in h2.receive_data(data):
                        if isinstance(event, RequestReceived):
                            requests[event.stream_id] = [
                                dict(event.headers),
                                bytearray(),
                            ]
                        elif isinstance(event, DataReceived):
                            requests[event.stream_id][1].extend(event.data)
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            headers, body = requests.pop(event.stream_id)
                            token = headers["authorization"]
                            self.attempts.append((token, bytes(body)))
                            if token == "Bearer a" or self.reject_both.is_set():
                                h2.send_headers(
                                    event.stream_id,
                                    [(":status", "429"), ("retry-after", "3600")],
                                )
                                h2.send_data(event.stream_id, b"{}", end_stream=True)
                            else:
                                h2.send_headers(
                                    event.stream_id,
                                    [
                                        (":status", "200"),
                                        ("content-type", "text/event-stream"),
                                    ],
                                )
                                h2.send_data(
                                    event.stream_id,
                                    b'data: {"type":"message_start"}\n\n',
                                )
                                connection.sendall(h2.data_to_send())
                                self.allow_finish.wait(5)
                                h2.send_data(
                                    event.stream_id,
                                    b'data: {"type":"message_stop"}\n\n',
                                    end_stream=True,
                                )
                    connection.sendall(h2.data_to_send())

    def shutdown(self):
        self.stopped.set()

    def server_close(self):
        self.socket.close()
        self.folder.cleanup()


class RetryTest(unittest.TestCase):
    def test_rejection_replays_once_and_preserves_streaming(self):
        self.run_retry(False)

    def test_http2_rejection_replays_once_and_preserves_streaming(self):
        self.run_retry(True)

    def run_retry(self, http2):
        client_type = H2Client if http2 else http.client.HTTPConnection
        attempts = []
        allow_finish = threading.Event()
        reject_both = threading.Event()

        class Upstream(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                body = b""
                if self.headers.get("Transfer-Encoding") == "chunked":
                    while length := int(self.rfile.readline().strip(), 16):
                        body += self.rfile.read(length)
                        self.rfile.read(2)
                    self.rfile.readline()
                else:
                    body = self.rfile.read(int(self.headers["Content-Length"]))
                token = self.headers["Authorization"]
                attempts.append((token, body))
                if token == "Bearer a" or reject_both.is_set():
                    self.send_response(429)
                    self.send_header("Content-Length", "2")
                    self.send_header("Retry-After", "3600")
                    self.end_headers()
                    self.wfile.write(b"{}")
                    self.wfile.flush()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b'data: {"type":"message_start"}\n\n')
                self.wfile.flush()
                allow_finish.wait(5)
                self.wfile.write(b'data: {"type":"message_stop"}\n\n')
                self.close_connection = True

        upstream = (
            H2Upstream(attempts, allow_finish, reject_both)
            if http2
            else HTTPServer(("127.0.0.1", 0), Upstream)
        )
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)
        self.addCleanup(allow_finish.set)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "credential").write_text("a")
            (root / "accounts" / "second").mkdir(parents=True)
            (root / "accounts" / "second" / "credential").write_text("b")
            addon = root / "fixture.py"
            addon.write_text(f"""
import sys
sys.path.insert(0, {str(PROXY_SOURCE)!r})
import billing_addon as billing
from cheese_billing_core import Verdict, NO_LOGIN_PLACEHOLDER
from pathlib import Path
load = billing.load
def running():
    Path({str(root / "ready")!r}).touch()
responseheaders = billing.responseheaders
response = billing.response
error = billing.error
client_disconnected = billing.client_disconnected
def requestheaders(flow):
    flow.metadata["cheese_attr"] = ("project", "session")
    flow.request.headers["authorization"] = "Bearer " + NO_LOGIN_PLACEHOLDER
    flow.request.stream = True
    billing._route(flow, Verdict(True, ""), is_messages=True,
                   project_id="project", bearer="", keep_haiku=False)
""")
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                port = available.getsockname()[1]
            env = {
                **os.environ,
                "CHEESE_CLAUDE_CREDENTIAL": str(root / "credential"),
                "CHEESE_USAGE_LOG": str(root / "usage.jsonl"),
            }
            with (root / "proxy.log").open("w+") as log:
                proxy = subprocess.Popen(
                    [
                        "mitmdump",
                        "-q",
                        "--listen-host",
                        "127.0.0.1",
                        "--listen-port",
                        str(port),
                        "--mode",
                        f"reverse:{'https' if http2 else 'http'}://127.0.0.1:{upstream.server_port}",
                        "--ssl-insecure",
                        "--set",
                        f"confdir={root / 'ca'}",
                        "--set",
                        "connection_strategy=lazy",
                        "-s",
                        str(addon),
                    ],
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        try:
                            if not (root / "ready").exists():
                                raise OSError("proxy is still starting")
                            socket.create_connection(
                                ("127.0.0.1", port), timeout=0.1
                            ).close()
                            break
                        except OSError:
                            if time.monotonic() > deadline or proxy.poll() is not None:
                                raise RuntimeError("proxy did not start")
                            time.sleep(0.05)
                    request = json.dumps(
                        {
                            "model": "same-model",
                            "messages": [{"role": "user", "content": "hello"}],
                        }
                    ).encode()
                    client = client_type("127.0.0.1", port, timeout=5)
                    client.request("POST", "/v1/messages", request)
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(
                        response.readline(), b'data: {"type":"message_start"}\n'
                    )
                    # The first event must reach the client before upstream completes.
                    self.assertFalse(allow_finish.is_set())
                    allow_finish.set()
                    self.assertIn(b"message_stop", response.read())
                    client.close()
                    self.assertEqual(
                        attempts, [("Bearer a", request), ("Bearer b", request)]
                    )
                    client = client_type("127.0.0.1", port, timeout=5)
                    client.request("POST", "/v1/messages", request)
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                    client.close()
                    self.assertEqual(
                        [token for token, _ in attempts],
                        ["Bearer a", "Bearer b", "Bearer b"],
                    )
                    reject_both.set()
                    client = client_type("127.0.0.1", port, timeout=5)
                    client.request("POST", "/v1/messages", request)
                    response = client.getresponse()
                    self.assertEqual(response.status, 429)
                    response.read()
                    client.close()
                    count = len(attempts)
                    client = client_type("127.0.0.1", port, timeout=5)
                    client.request("POST", "/v1/messages", request)
                    response = client.getresponse()
                    self.assertEqual(response.status, 429)
                    self.assertIsNotNone(response.getheader("retry-after"))
                    response.read()
                    client.close()
                    self.assertEqual(len(attempts), count)
                finally:
                    proxy.terminate()
                    proxy.wait(timeout=5)
                    log.seek(0)
                    print(log.read())


if __name__ == "__main__":
    unittest.main()
