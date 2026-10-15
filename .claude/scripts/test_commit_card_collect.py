#!/usr/bin/env python3
"""Offline tests for conservative platform tool classification."""

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from commit_card_collect import classify, collect_line, observation, read_api


class ClassificationTests(unittest.TestCase):
    def test_redirection_is_not_background(self):
        phase, rule = classify("Bash", "pytest -q 2>&1 | tail -20")
        self.assertEqual((phase, rule), ("local_check", "simple-shell-local_check"))

    def test_standalone_ampersand_is_background(self):
        phase, rule = classify("Bash", "pytest -q &")
        self.assertEqual((phase, rule), ("other_tool", "shell-background"))

    def test_nohup_without_ampersand_is_foreground(self):
        phase, rule = classify("Bash", "nohup pytest -q")
        self.assertEqual((phase, rule), ("local_check", "simple-shell-local_check"))

    def test_mentions_are_not_checks(self):
        for command in ("rg pytest", "which pytest", "git log --grep pytest",
                        "grep pytest Makefile"):
            self.assertEqual(classify("Bash", command)[0], "other_tool", command)

    def test_supported_check_entrypoints(self):
        for command, phase in (
            ("pytest -q", "local_check"),
            ("ruff check .", "local_check"),
            ("task ci:fast", "local_check"),
            ("python3 -m unittest discover", "local_check"),
            ("python3 .github/scripts/ci-fast.py", "local_check"),
            ("pnpm lint", "local_check"),
            ("npm run typecheck", "local_check"),
            ("gh pr checks 3785 --watch", "ci_wait"),
            ("gh run watch 123", "ci_wait"),
            ("sleep 2", "explicit_wait"),
        ):
            self.assertEqual(classify("Bash", command)[0], phase, command)

    def test_background_output_removes_unbounded_end(self):
        row = {
            "conversation_id": "topic", "id": "block", "created_at": "2026-10-08T10:10:00Z",
            "meta": {"tool": "Bash", "detail": "pytest -q", "output_bytes": 10,
                     "at": "2026-10-08T10:11:00Z"},
        }
        result = observation(row, "Command running in background with session ID 7")
        self.assertIsNone(result["end"])
        self.assertEqual(result["qualification"], "background-completion-unavailable")


class CollectionTests(unittest.TestCase):
    def test_history_pages_embedded_output_sanitized_and_checkpointed(self):
        line = {"topic": "topic", "task": 1, "start": "2026-10-08T10:10:00Z",
                "end": "2026-10-08T10:20:00Z"}
        row = {"id": "b", "conversation_id": "topic", "created_at": "2026-10-08T10:09:00Z",
               "meta": {"tool": "Bash", "detail": "pytest -q", "output": "secret test output",
                        "output_bytes": 18, "at": "2026-10-08T10:12:00Z"}}
        pages = [{"data": [], "has_more": True, "oldest_id": "older"},
                 {"data": [row], "has_more": False}]
        # An empty page with has_more is rejected, not silently labelled complete.
        with tempfile.TemporaryDirectory() as tmp, patch("commit_card_collect.read_api", side_effect=pages):
            with self.assertRaisesRegex(ValueError, "pagination"):
                collect_line(["token"], line, Path(tmp))
        pages[0]["data"] = [{"id": "new", "conversation_id": "topic", "meta": {},
                             "created_at": "2026-10-08T10:30:00Z"}]
        with tempfile.TemporaryDirectory() as tmp, patch("commit_card_collect.read_api", side_effect=pages) as api:
            result = collect_line(["token"], line, Path(tmp))
            self.assertEqual(api.call_count, 2)
            self.assertIn("/history?limit=200&kind=event", api.call_args_list[0].args[1])
            self.assertIn("before=older", api.call_args_list[1].args[1])
            self.assertTrue(result["complete"])
            self.assertEqual(result["observations"][0]["end"], row["meta"]["at"])
            self.assertNotIn("secret test output", json.dumps(result))
            self.assertNotIn("pytest -q", json.dumps(result))
            self.assertEqual(collect_line(["token"], line, Path(tmp)), result)
            self.assertEqual(api.call_count, 2)
            path = Path(tmp) / "topic.json"
            result["collector_version"] = -1
            path.write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "version"):
                collect_line(["token"], line, Path(tmp))

    @patch("commit_card_collect.time.sleep")
    def test_expired_token_refreshes_once(self, sleep):
        error = urllib.error.HTTPError("test", 401, "expired", {}, None)
        token = ["old"]
        with patch("commit_card_collect.get", side_effect=[error, {"data": []}]) as get, \
             patch("commit_card_collect.login", return_value="new") as login:
            self.assertEqual(read_api(token, "/history"), {"data": []})
        self.assertEqual(token, ["new"])
        login.assert_called_once()
        self.assertEqual(get.call_args.args[0], "new")

    @patch("commit_card_collect.time.sleep")
    def test_rate_limit_does_not_consume_token_refresh(self, sleep):
        rate = urllib.error.HTTPError("test", 429, "limited", {"Retry-After": "1"}, None)
        expired = urllib.error.HTTPError("test", 401, "expired", {}, None)
        token = ["old"]
        with patch("commit_card_collect.get", side_effect=[rate, expired, {"data": []}]) as get, \
             patch("commit_card_collect.login", return_value="new") as login:
            self.assertEqual(read_api(token, "/history"), {"data": []})
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args.args[0], "new")
        login.assert_called_once()
        with patch("commit_card_collect.get", side_effect=[rate, expired, expired]) as get, \
             patch("commit_card_collect.login", return_value="new") as login:
            with self.assertRaises(urllib.error.HTTPError):
                read_api(["old"], "/history")
        self.assertEqual(get.call_count, 3)
        login.assert_called_once()

    @patch("commit_card_collect.time.sleep")
    def test_refused_permission_is_not_retried(self, sleep):
        error = urllib.error.HTTPError("test", 403, "denied", {}, None)
        with patch("commit_card_collect.get", side_effect=error) as get:
            with self.assertRaises(urllib.error.HTTPError):
                read_api(["token"], "/history")
        get.assert_called_once()

    @patch("commit_card_collect.time.sleep")
    def test_rate_limit_retry_is_bounded(self, sleep):
        error = urllib.error.HTTPError("test", 429, "rate limit", {"Retry-After": "1"}, None)
        with patch("commit_card_collect.get", side_effect=error) as get:
            with self.assertRaises(urllib.error.HTTPError):
                read_api(["token"], "/history")
        self.assertEqual(get.call_count, 4)


if __name__ == "__main__":
    unittest.main()
