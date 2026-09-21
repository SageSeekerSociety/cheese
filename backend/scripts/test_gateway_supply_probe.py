"""Provider failures must stay distinguishable without revealing response contents."""

import contextlib
import http.client
import importlib.util
import io
import json
import os
import socket
import ssl
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "probe", Path(__file__).with_name("gateway_supply_probe.py")
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Response(io.BytesIO):
    def __init__(self, body, sse=False):
        super().__init__(body)
        self.headers = {
            "Content-Type": "text/event-stream" if sse else "application/json"
        }


def stream(terminal=True, stop="end_turn"):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
        {"type": "content_block_delta", "delta": {"text": "secret-model-output"}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop},
            "usage": {"output_tokens": 1},
        },
    ]
    if terminal:
        events.append({"type": "message_stop"})
    return b"".join(
        b"data: " + json.dumps(event).encode() + b"\n\n" for event in events
    )


class ProbeTests(unittest.TestCase):
    def run_probe(self, replies, args=()):
        output, requests = io.StringIO(), []

        def send(request, timeout):
            requests.append(request)
            reply = replies[len(requests) - 1]
            if isinstance(reply, Exception):
                raise reply
            return reply

        with (
            patch.dict(os.environ, {"LITELLM_MASTER_KEY": "secret-key"}),
            patch.object(probe.urllib.request, "urlopen", send),
            contextlib.redirect_stdout(output),
        ):
            code = probe.main(args)
        self.assertNotIn("secret", output.getvalue())
        return (
            code,
            [json.loads(line) for line in output.getvalue().splitlines()],
            requests,
        )

    def ready(self):
        return [
            Response(b'{"status":"healthy","db":"connected"}'),
            Response(b'{"data":[{"model_name":"test-model"}]}'),
        ]

    def test_default_is_authenticated_readiness_without_generation(self):
        code, rows, requests = self.run_probe(self.ready())
        self.assertEqual(code, 0)
        self.assertEqual([r.get_method() for r in requests], ["GET", "GET"])
        self.assertTrue(
            all(r.get_header("Authorization") == "Bearer secret-key" for r in requests)
        )
        self.assertEqual(rows[1]["inference"], "not_checked")

    def test_database_failure_is_not_green(self):
        code, rows, requests = self.run_probe([Response(b'{"db":"disconnected"}')])
        self.assertEqual(code, 1)
        self.assertEqual(rows[-1]["category"], "gateway_dependency")
        self.assertEqual(len(requests), 1)

    def test_transport_errors_are_not_quota(self):
        for error, category in (
            (socket.gaierror(-2, "secret-host"), "dns"),
            (ssl.SSLCertVerificationError("secret-cert"), "tls"),
            (TimeoutError("secret-timeout"), "timeout"),
        ):
            with self.subTest(category=category):
                code, rows, _ = self.run_probe([urllib.error.URLError(error)])
                self.assertEqual((code, rows[-1]["category"]), (1, category))

    def test_http_categories_do_not_guess_network_root_cause(self):
        for status, body, category in (
            (
                429,
                b'{"error":{"code":"1113","message":"secret-account"}}',
                "provider_quota",
            ),
            (429, b'{"error":{"message":"secret-limited"}}', "rate_limit"),
            (401, b"secret", "http_auth"),
            (500, b"secret-dns?", "http_error"),
        ):
            with self.subTest(category=category):
                error = urllib.error.HTTPError(
                    "http://secret", status, "secret", {}, io.BytesIO(body)
                )
                code, rows, _ = self.run_probe(
                    self.ready() + [error], ["--generate", "--model", "test-model"]
                )
                self.assertEqual((code, rows[-1]["category"]), (1, category))

    def test_native_stream_requires_terminal_event(self):
        for terminal, stop, expected in (
            (True, "end_turn", None),
            (False, "end_turn", "stream_incomplete"),
            (True, "max_tokens", "output_limit"),
        ):
            with self.subTest(terminal=terminal, stop=stop):
                code, rows, requests = self.run_probe(
                    self.ready() + [Response(stream(terminal, stop), True)],
                    ["--generate", "--model", "test-model"],
                )
                self.assertEqual(len(requests), 3)
                self.assertEqual(
                    requests[-1].full_url, "http://127.0.0.1:4000/v1/messages"
                )
                self.assertEqual(code, int(expected is not None))
                if expected:
                    self.assertEqual(rows[-1]["category"], expected)

    def test_no_implicit_model_or_generation(self):
        for args in (["--generate"], ["--model", "test-model"]):
            with (
                self.subTest(args=args),
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(probe.urllib.request, "urlopen") as send,
            ):
                with self.assertRaises(SystemExit):
                    probe.main(args)
                send.assert_not_called()

    def test_chunked_transport_truncation(self):
        class Truncated(Response):
            def readline(self, limit=-1):
                raise http.client.IncompleteRead(b"secret partial content")

        code, rows, _ = self.run_probe(
            self.ready() + [Truncated(b"", True)],
            ["--generate", "--model", "test-model"],
        )
        self.assertEqual((code, rows[-1]["category"]), (1, "stream_transport"))

    def test_terminal_event_does_not_wait_for_connection_close(self):
        class HeldOpen(Response):
            def readline(self, limit=-1):
                line = super().readline(limit)
                if not line:
                    raise TimeoutError("server kept connection open")
                return line

        code, _, _ = self.run_probe(
            self.ready() + [HeldOpen(stream(), True)],
            ["--generate", "--model", "test-model"],
        )
        self.assertEqual(code, 0)

    def test_invalid_catalog_and_sse_shapes(self):
        replies = self.ready()
        replies[1] = Response(b'{"data":[{}]}')
        code, rows, _ = self.run_probe(replies)
        self.assertEqual((code, rows[-1]["category"]), (1, "invalid_catalog"))
        for event in ([], {"type": "message_start", "message": None}, {"usage": []}):
            with self.subTest(event=event):
                body = b"data: " + json.dumps(event).encode() + b"\n\n"
                code, rows, _ = self.run_probe(
                    self.ready() + [Response(body, True)],
                    ["--generate", "--model", "test-model"],
                )
                self.assertEqual((code, rows[-1]["category"]), (1, "invalid_response"))

    def test_safe_call_id_is_preserved_on_success_and_failure(self):
        identifier = "494dce66-5728-446b-b81a-70170af222fa"
        for terminal in (True, False):
            response = Response(stream(terminal), True)
            response.headers["x-litellm-call-id"] = identifier
            code, rows, _ = self.run_probe(
                self.ready() + [response], ["--generate", "--model", "test-model"]
            )
            record = next(
                row for row in rows if row["phase"] in ("inference", "failed")
            )
            self.assertEqual(record["call_id"], identifier)
        self.assertIsNone(
            probe.call_id_from({"x-litellm-call-id": "secret arbitrary header"})
        )


if __name__ == "__main__":
    unittest.main()
