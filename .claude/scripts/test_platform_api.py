#!/usr/bin/env python3
"""The two scripts that talk to the platform use one client, not two.

`seat_tps.py` and `wake_orchestrator.py` each carried their own `call`, `unwrap` and
`login`, byte for byte, differing only in `call`'s default timeout. Nothing compared
them, so a fix to one — a retry, a header, a field the login answer moved — left the
other on the old client and both self-tests green. The identity assertions below are the
half that catches a copy coming back; the rest pin the behaviour a shared client has to
keep for either caller, driven through a stubbed socket rather than the network.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import platform_api  # noqa: E402
import seat_tps  # noqa: E402
import wake_orchestrator  # noqa: E402


class OneClient(unittest.TestCase):
    def test_both_scripts_use_the_shared_client(self) -> None:
        for name in ("call", "unwrap", "login"):
            for script in (seat_tps, wake_orchestrator):
                self.assertIs(
                    getattr(script, name), getattr(platform_api, name),
                    f"{script.__name__}.{name} is not the shared one",
                )

    def test_both_scripts_name_the_same_dev_account(self) -> None:
        self.assertIs(wake_orchestrator.USER, platform_api.USER)
        self.assertIs(seat_tps.USER, platform_api.USER)

    def test_wake_self_test_stubs_the_client_login_uses(self) -> None:
        """`login` resolves `call` in `platform_api`, so a self-test that patched only
        its own binding would leave the login call on the network. Drive the self-test
        with the real `call` replaced by one that refuses, and require it to stay green:
        a stub that misses leaves the run talking to okcheese.com."""
        def refuse(*_args, **_kwargs):
            raise AssertionError("a self-test reached the network")

        with patch.object(platform_api, "call", refuse), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wake_orchestrator.self_test(), 0)


class TheClient(unittest.TestCase):
    """Behaviour a caller can observe at the socket, with no platform involved."""

    def reply(self, body: bytes, seen: list):
        class Response:
            def read(self_inner):
                return body

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_):
                return False

        def urlopen(request, timeout=None):
            seen.append({"request": request, "timeout": timeout})
            return Response()

        return urlopen

    def test_login_reads_a_token_out_of_a_nested_data_envelope(self) -> None:
        seen: list = []
        body = json.dumps({"data": {"data": {"accessToken": "tok"}}}).encode()
        with patch.object(platform_api.urllib.request, "urlopen", self.reply(body, seen)):
            self.assertEqual(platform_api.login("someone", "pw"), "tok")
        self.assertEqual(seen[0]["request"].get_full_url(),
                         platform_api.API + "/users/auth/login")

    def test_an_empty_body_is_an_empty_answer_not_a_parse_error(self) -> None:
        seen: list = []
        with patch.object(platform_api.urllib.request, "urlopen", self.reply(b"", seen)):
            self.assertEqual(platform_api.call("GET", "/x"), {})

    def test_the_default_timeout_is_thirty_seconds(self) -> None:
        seen: list = []
        token_body = json.dumps({"data": {"accessToken": "tok"}}).encode()
        with patch.object(platform_api.urllib.request, "urlopen",
                          self.reply(token_body, seen)):
            platform_api.call("GET", "/x")
            platform_api.login("someone", "pw", timeout=60)
        self.assertEqual([call["timeout"] for call in seen], [30, 60])

    def test_a_token_is_sent_as_a_bearer_header_and_omitted_when_there_is_none(self) -> None:
        seen: list = []
        with patch.object(platform_api.urllib.request, "urlopen", self.reply(b"{}", seen)):
            platform_api.call("GET", "/x", "tok")
            platform_api.call("GET", "/x")
        self.assertEqual(seen[0]["request"].get_header("Authorization"), "Bearer tok")
        self.assertIsNone(seen[1]["request"].get_header("Authorization"))

    def test_seat_tps_asks_for_the_longer_budget_it_used_to_default_to(self) -> None:
        """The throughput call aggregates a window and was the 60-second caller; folding
        the two clients must not quietly cut it to the shared default of 30."""
        seen: list = []

        def fake_call(method, path, token=None, body=None, timeout=30):
            seen.append(timeout)
            return {}

        with patch.object(seat_tps, "call", fake_call), \
                patch.object(seat_tps, "login", lambda *a, **k: "tok"), \
                patch.dict("os.environ", {"OKCHEESE_PASS": "pw"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(seat_tps.main(["--topic", "t"]), 0)
        self.assertEqual(seen, [60])


if __name__ == "__main__":
    unittest.main()
