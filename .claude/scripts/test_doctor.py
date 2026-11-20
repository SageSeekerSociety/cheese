from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("doctor.py")
spec = importlib.util.spec_from_file_location("doctor", SCRIPT)
doctor = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = doctor
spec.loader.exec_module(doctor)


class StubHandler(BaseHTTPRequestHandler):
    scenario = "green"
    received_session_auth = False
    paths: list[str] = []

    def log_message(self, *_args):
        pass

    def reply(self, status: int, payload: dict, headers: dict[str, str] | None = None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.paths.append(self.path)
        if self.path == "/sandbox/forge-token":
            type(self).received_session_auth = (
                self.headers.get("Authorization") == "Bearer session-secret"
            )
            data = {
                "kind": "github_app",
                "token": "app-secret",
                "repo": "Acme/Widget",
                "api_url": f"http://127.0.0.1:{self.server.server_port}/gh",
                "permissions": "contents: write, metadata: read",
            }
            if self.scenario == "invalid-api-url":
                data["api_url"] = 1
            if self.scenario == "permissions-omitted":
                data.pop("permissions")
            elif self.scenario == "write-denied":
                data["permissions"] = "contents: read, metadata: read"
            return self.reply(200, {"data": data})
        if self.path == "/gh/rate_limit":
            if self.scenario == "rate-limited":
                return self.reply(
                    403,
                    {"message": "API rate limit exceeded"},
                    {"X-RateLimit-Remaining": "0"},
                )
            if self.scenario == "unavailable":
                return self.reply(503, {"message": "service unavailable"})
            return self.reply(
                200, {"resources": {"core": {"remaining": 31, "limit": 60}}}
            )
        if self.path == "/gh/repos/Acme/Widget":
            if self.scenario == "repo-hidden":
                return self.reply(404, {"message": "Not Found"})
            return self.reply(
                200,
                {
                    "full_name": "Acme/Widget",
                    "permissions": {
                        "admin": False,
                        "maintain": False,
                        "pull": False,
                        "push": False,
                        "triage": False,
                    },
                },
            )
        return self.reply(404, {"message": "unexpected path"})

    def do_POST(self):
        self.paths.append(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.scenario == "graphql-rate-limited":
            return self.reply(
                200,
                {
                    "errors": [
                        {"type": "RATE_LIMITED", "message": "API rate limit exceeded"}
                    ]
                },
                {"X-RateLimit-Remaining": "0"},
            )
        return self.reply(
            200,
            {
                "data": {
                    "viewer": {"login": "cheese-bot"},
                    "rateLimit": {"remaining": 4800, "resetAt": "2026-10-16T23:00:00Z"},
                }
            },
        )


class DoctorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        StubHandler.scenario = "green"
        StubHandler.received_session_auth = False
        StubHandler.paths = []
        self.env = patch.dict(
            os.environ,
            {
                "CHEESE_API": self.base,
                "CHEESE_TOKEN": "session-secret",
                "GITHUB_REPOSITORY": "Acme/Widget",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_doctor(self, args: tuple[str, ...] = ("github",)) -> tuple[int, str]:
        output = io.StringIO()
        with (
            patch.object(doctor.sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(output),
        ):
            result = doctor.main()
        return result, output.getvalue()

    def test_both_layers_pass_with_installation_token_and_default_command(self):
        result, output = self.run_doctor(args=())
        self.assertEqual(result, 0, output)
        self.assertIn("[1] GitHub API: OK", output)
        self.assertIn("REST core remaining=31", output)
        self.assertIn("GraphQL remaining=4800", output)
        self.assertIn("[2] Repository access: OK", output)
        self.assertTrue(StubHandler.received_session_auth)
        self.assertIn("/gh/repos/Acme/Widget", StubHandler.paths)

    def test_first_layer_rate_limit_stops_before_repository_check(self):
        StubHandler.scenario = "rate-limited"
        result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("rate limit is exhausted", output)
        self.assertIn("Repository access: NOT RUN", output)
        self.assertNotIn("/gh/repos/Acme/Widget", StubHandler.paths)

    def test_first_layer_graphql_rate_limit_is_failure(self):
        StubHandler.scenario = "graphql-rate-limited"
        result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("GraphQL API rate limit is exhausted", output)
        self.assertIn("Repository access: NOT RUN", output)
        self.assertNotIn("/gh/repos/Acme/Widget", StubHandler.paths)

    def test_first_layer_service_unavailable_is_failure(self):
        StubHandler.scenario = "unavailable"
        result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("returned 503", output)
        self.assertIn("Repository access: NOT RUN", output)

    def test_first_layer_connection_failure_is_failure(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            dead_port = sock.getsockname()[1]
        with patch.object(
            doctor,
            "get_installation",
            return_value=(
                "app-secret",
                "Acme/Widget",
                f"http://127.0.0.1:{dead_port}/gh",
                f"http://127.0.0.1:{dead_port}/gh/graphql",
                "contents: write, metadata: read",
            ),
        ):
            output = io.StringIO()
            with (
                patch.object(doctor.sys, "argv", [str(SCRIPT), "github"]),
                contextlib.redirect_stdout(output),
            ):
                result = doctor.main()
        self.assertEqual(result, 1)
        self.assertIn("connection or response failed", output.getvalue())
        self.assertIn("Repository access: NOT RUN", output.getvalue())

    def test_invalid_api_url_is_reported_without_traceback(self):
        StubHandler.scenario = "invalid-api-url"
        result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("invalid REST API URL", output)
        self.assertIn("Repository access: NOT RUN", output)
        self.assertNotIn("Traceback", output)

    def test_second_layer_hidden_repository_is_failure_even_after_api_passes(self):
        StubHandler.scenario = "repo-hidden"
        result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("[1] GitHub API: OK", output)
        self.assertIn("Repository access: FAIL", output)
        self.assertIn("HTTP 404", output)

    def test_second_layer_read_only_installation_fails_write_check(self):
        StubHandler.scenario = "write-denied"
        result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("Repository access: FAIL", output)
        self.assertIn("lacks contents:write", output)

    def test_second_layer_missing_permissions_is_not_silent_success(self):
        StubHandler.scenario = "permissions-omitted"
        result, output = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn("[1] GitHub API: OK", output)
        self.assertIn("Repository access: FAIL", output)
        self.assertIn("permissions are missing or malformed", output)


if __name__ == "__main__":
    unittest.main()
