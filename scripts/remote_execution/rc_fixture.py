"""Loopback-only RC protocol fixture; all credentials and model output are fake."""

import gzip
import json
import queue
import socket
import ssl
import subprocess
import threading
import uuid
from urllib.parse import urlparse


class RemoteControlFixture:
    def __init__(self, root, record):
        self.root, self.record = root, record
        self.sid = "cse_" + uuid.uuid4().hex[:24]
        self.events = queue.Queue()
        self.responses = []
        self.connected = threading.Event()
        self.sequence = 0
        self.http = []
        self.next_tool = None
        self.title = "Isolated RC fixture"
        self.stream_count = 0
        self.bridge_count = 0
        self.expires_in = 3600
        self.strict = False
        self.fault = None
        self.base = None
        self.flags = {
            "tengu_ccr_bridge": True,
            "tengu_ccr_v2_bridge_create_cli": True,
            "tengu_ccr_v2_session_crud_cli": True,
            "tengu_cobalt_harbor": False,
        }
        self.cert = root / "fixture-ca.pem"
        key = root / "fixture-key.pem"
        conf = root / "fixture-cert.cnf"
        conf.write_text(
            "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n[dn]\nCN=RC Local Fixture\n[ext]\nbasicConstraints=critical,CA:TRUE\nsubjectAltName=DNS:api.anthropic.com,DNS:platform.claude.com,DNS:claude.ai,DNS:cdn.growthbook.io,DNS:api.statsig.com,DNS:statsig.anthropic.com\n"
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-keyout",
                str(key),
                "-out",
                str(self.cert),
                "-config",
                str(conf),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(self.cert, key)

    def send(self, payload):
        self.sequence += 1
        payload = {"uuid": str(uuid.uuid4()), **payload}
        event = {
            "event_id": str(uuid.uuid4()),
            "sequence_num": str(self.sequence),
            "event_type": payload["type"],
            "source": "client",
            "client_platform": "web_claude_ai",
            "payload": payload,
        }
        self.events.put(event)
        self.record("rc_input", frame=event)

    def response(self, request_id):
        return next(
            (
                e
                for e in self.responses
                if e.get("response", {}).get("request_id") == request_id
            ),
            None,
        )

    def handler(self, parent):
        fixture = self

        class Handler(parent):
            def do_CONNECT(self):
                fixture.record("proxy_connect", target=self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.flush()
                try:
                    self.connection = fixture.tls.wrap_socket(
                        self.connection, server_side=True
                    )
                    self.rfile = self.connection.makefile("rb", self.rbufsize)
                    self.wfile = self.connection.makefile("wb", self.wbufsize)
                    self.handle()
                except (OSError, ssl.SSLError) as exc:
                    fixture.record("proxy_closed", error=str(exc))

            def reply(self, obj, status=200):
                data = json.dumps(obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def route(self, method, body=None):
                path = urlparse(self.path).path
                captured_headers = {
                    k: v
                    for k, v in self.headers.items()
                    if k.lower()
                    not in (
                        "authorization",
                        "cookie",
                        "x-api-key",
                        "x-trusted-device-token",
                    )
                }
                row = {
                    "method": method,
                    "path": path,
                    "target": self.path,
                    "server_role": getattr(self.server, "audit_role", "combined"),
                    "headers": captured_headers,
                    "body": body,
                }
                fixture.http.append(row)
                fixture.record(
                    "rc_http",
                    method=method,
                    path=path,
                    body=body,
                    headers=captured_headers,
                    server_role=row["server_role"],
                )
                if fixture.fault and path.endswith(fixture.fault["suffix"]):
                    fault = fixture.fault
                    fixture.fault = None
                    fixture.record(
                        "injected_http_fault", status=fault["status"], path=path
                    )
                    return self.reply(
                        {
                            "error": {
                                "type": "authentication_error",
                                "message": "isolated fixture fault",
                            }
                        },
                        fault["status"],
                    )
                if path.endswith("/worker/events/stream"):
                    fixture.stream_count += 1
                    stream_number = fixture.stream_count
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    fixture.connected.set()
                    while True:
                        try:
                            event = fixture.events.get(timeout=5)
                            if stream_number != fixture.stream_count:
                                fixture.events.put(event)
                                fixture.record(
                                    "stale_stream_requeued", stream_number=stream_number
                                )
                                self.close_connection = True
                                return
                            if event is None:
                                self.close_connection = True
                                self.connection.shutdown(socket.SHUT_RDWR)
                                return
                            wire = (
                                "event: client_event\nid: "
                                + event["sequence_num"]
                                + "\ndata: "
                                + json.dumps(event)
                                + "\n\n"
                            )
                        except queue.Empty:
                            wire = ": heartbeat\n\n"
                        try:
                            self.wfile.write(wire.encode())
                            self.wfile.flush()
                        except OSError:
                            return
                if path.endswith("/worker/events"):
                    for event in (body or {}).get("events", []):
                        payload = event.get("payload", {})
                        fixture.responses.append(payload)
                    return self.reply({})
                if path.endswith("/policy_limits"):
                    return self.reply({"restrictions": {}})
                if "/api/eval" in path:
                    return self.reply(
                        {
                            "features": {
                                k: {"defaultValue": v} for k, v in fixture.flags.items()
                            }
                        }
                    )
                if path.endswith("/bridge") and "/sessions/" in path:
                    fixture.bridge_count += 1
                    return self.reply(
                        {
                            "worker_jwt": "fake-rc-worker-token-"
                            + str(fixture.bridge_count),
                            "worker_epoch": fixture.bridge_count,
                            "api_base_url": fixture.base,
                            "expires_in": fixture.expires_in,
                        }
                    )
                if path in ("/v1/code/sessions", "/v1/sessions") and method == "POST":
                    return self.reply(
                        {
                            "session": {
                                "id": fixture.sid,
                                "title": "Isolated RC fixture",
                                "status": "active",
                                "environment_kind": "bridge",
                                "config": {},
                                "created_at": "2026-09-08T00:00:00Z",
                            }
                        }
                    )
                if path.endswith("/" + fixture.sid) and method == "PUT":
                    fixture.title = body.get("title", fixture.title)
                    return self.reply(
                        {"id": fixture.sid, "title": fixture.title, "status": "active"}
                    )
                if "/sessions/" in path and path.endswith("/worker"):
                    return self.reply(
                        {"worker": {"external_metadata": {}, "internal_metadata": {}}}
                    )
                if "/sessions/" in path and method == "GET":
                    return self.reply(
                        {"id": fixture.sid, "status": "active", "data": []}
                    )
                if path.endswith("/profile"):
                    return self.reply(
                        {
                            "account": {
                                "uuid": "00000000-0000-4000-8000-000000000001",
                                "email": "probe@example.invalid",
                            },
                            "organization": {
                                "uuid": "00000000-0000-4000-8000-000000000002",
                                "organization_type": "claude_max",
                                "billing_type": "stripe_subscription",
                            },
                        }
                    )
                allowed_empty = (
                    "/api/claude_cli/bootstrap",
                    "/api/claude_code_grove",
                    "/api/claude_code_penguin_mode",
                    "/api/event_logging/v2/batch",
                    "/api/oauth/usage",
                )
                if path in allowed_empty or path.endswith(
                    (
                        "/referral/eligibility",
                        "/worker/heartbeat",
                        "/worker/events/delivery",
                        "/client/presence",
                        "/archive",
                        "/unarchive",
                    )
                ):
                    return self.reply({})
                fixture.record("unimplemented_http_route", method=method, path=path)
                return self.reply(
                    {
                        "error": {
                            "type": "not_found",
                            "message": "fixture route not implemented",
                        }
                    },
                    404 if fixture.strict else 200,
                )

            def do_GET(self):
                return self.route("GET")

            def do_POST(self):
                if urlparse(self.path).path in (
                    "/v1/messages",
                    "/hook",
                    "/topics/fixture/messages",
                ):
                    return super().do_POST()
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return self.route("POST", json.loads(raw or b"{}"))

            def do_PUT(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                return self.route("PUT", json.loads(raw or b"{}"))

        return Handler
