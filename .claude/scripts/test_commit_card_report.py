#!/usr/bin/env python3
"""Offline evidence normalization and GitHub reader regression tests."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commit_card_ci import fetch_pages, main as collect_ci, observations, snapshot
from commit_card_report import combine


class EvidenceTests(unittest.TestCase):
    def test_contradictory_timestamps_keep_raw_evidence_and_do_not_invent_duration(self):
        event = {"topic": "one", "source": "github-check:1", "phase": "ci",
                 "start": "2026-10-08T10:12:01Z", "end": "2026-10-08T10:12:00Z"}
        raw = {"provenance": {}, "observations": [event]}
        combined, bad = combine(raw, {"provenance": {}, "observations": []})
        self.assertEqual(event["end"], "2026-10-08T10:12:00Z")
        self.assertIsNone(combined["observations"][0]["end"])
        self.assertEqual(bad[0]["source"], "github-check:1")
        self.assertEqual(combined["observations"][0]["recorded_end"], event["end"])

    def test_report_keeps_invalid_original_windows_separate(self):
        line = {"task": 1, "topic": "one", "start": "2026-10-08T10:10:00Z",
                "end": "2026-10-08T10:20:00Z"}
        invalid = {**line, "topic": "two", "task": 2, "start": "2026-10-08T10:21:00Z"}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "cohort.json").write_text(json.dumps({"lines": [line, invalid]}))
            (p / "empty.json").write_text(json.dumps({"provenance": {}, "observations": []}))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("commit_card_report.py")),
                                     str(p / "cohort.json"), str(p / "empty.json"), str(p / "empty.json"),
                                     "--out", str(p / "out")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads((p / "out/result.json").read_text())
            self.assertEqual(output["n"], 1)
            self.assertEqual(output["source_cohort_n"], 2)
            self.assertEqual(output["wall_seconds"], 600)
            self.assertEqual(output["invalid_cohort_lines"][0]["task"], 2)

    def test_report_rejects_foreign_evidence_topics(self):
        line = {"task": 1, "topic": "one", "start": "2026-10-08T10:10:00Z",
                "end": "2026-10-08T10:20:00Z"}
        event = {"topic": "foreign", "source": "github-check:9", "phase": "ci",
                 "start": line["start"], "end": line["end"]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "cohort.json").write_text(json.dumps({"lines": [line]}))
            (p / "platform.json").write_text(json.dumps({"provenance": {}, "observations": [event]}))
            (p / "ci.json").write_text(json.dumps({"provenance": {}, "observations": []}))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("commit_card_report.py")),
                                     str(p / "cohort.json"), str(p / "platform.json"), str(p / "ci.json"),
                                     "--out", str(p / "out")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outside the frozen cohort", result.stderr)
            self.assertFalse((p / "out").exists())

    def test_ci_pages_include_all_attempts(self):
        response = subprocess.CompletedProcess([], 0, json.dumps([{"check_runs": [{"id": 1}]},
                                                                  {"check_runs": [{"id": 2}]}]), "")
        with patch("commit_card_ci.subprocess.run", return_value=response) as run:
            self.assertEqual(fetch_pages("checks?filter=all", "check_runs"), [{"id": 1}, {"id": 2}])
        self.assertIn("--paginate", run.call_args.args[0])

    def test_ci_main_requests_all_check_attempts(self):
        line = {"task": 1, "topic": "one", "start": "2026-10-08T10:10:00Z",
                "end": "2026-10-08T10:20:00Z", "prs": [7]}
        calls = []
        def fake_fetch(endpoint, key=None):
            calls.append(endpoint)
            if "/pulls/7/commits" in endpoint:
                return [{"sha": "abc", "commit": {"committer": {"date": "2026-10-08T10:11:00Z"}}}]
            self.assertIn("filter=all", endpoint)
            return [{"id": 1, "name": "tests", "head_sha": "abc",
                     "started_at": "2026-10-08T10:12:00Z",
                     "completed_at": "2026-10-08T10:13:00Z", "conclusion": "failure",
                     "status": "completed"},
                    {"id": 2, "name": "tests", "head_sha": "abc",
                     "started_at": "2026-10-08T10:14:00Z",
                     "completed_at": "2026-10-08T10:15:00Z", "conclusion": "success",
                     "status": "completed"}]
        with tempfile.TemporaryDirectory() as tmp, patch("commit_card_ci.fetch_pages", side_effect=fake_fetch):
            with patch.object(sys, "argv", ["commit_card_ci.py", str(Path(tmp) / "cohort.json"),
                                             "--out", str(Path(tmp) / "out")]):
                (Path(tmp) / "cohort.json").write_text(json.dumps({"lines": [line]}))
                collect_ci()
            evidence = json.loads((Path(tmp) / "out/evidence.json").read_text())
        self.assertEqual({row["source"] for row in evidence["observations"]},
                         {"github-check:1", "github-check:2"})
        self.assertTrue(any("filter=all" in endpoint for endpoint in calls))

    @patch("commit_card_ci.time.sleep")
    def test_transient_failure_retries_but_permission_failure_does_not(self, sleep):
        retry = subprocess.CompletedProcess([], 1, "", 'Get "https://api.github.com/example": EOF')
        ok = subprocess.CompletedProcess([], 0, "[[]]", "")
        with patch("commit_card_ci.subprocess.run", side_effect=[retry, ok]) as run:
            self.assertEqual(fetch_pages("test"), [])
            self.assertEqual(run.call_count, 2)
        denied = subprocess.CompletedProcess([], 1, "", "HTTP 403: forbidden")
        with patch("commit_card_ci.subprocess.run", return_value=denied) as run:
            with self.assertRaisesRegex(RuntimeError, "403"):
                fetch_pages("test")
            run.assert_called_once()

    def test_ci_running_record_stays_unbounded_and_duplicates_do_not_sum(self):
        line = {"topic": "one", "start": "2026-10-08T10:10:00Z", "end": "2026-10-08T10:20:00Z"}
        run = {"id": 1, "name": "tests", "head_sha": "abc", "started_at": "2026-10-08T10:12:00Z",
               "completed_at": None, "conclusion": None}
        result = observations(line, [run, run])
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["end"])
        self.assertEqual(observations(line, [{**run, "conclusion": "skipped"}]), [])
        completed = {**run, "completed_at": "2026-10-08T10:15:00Z", "conclusion": "neutral"}
        neutral = observations(line, [completed])
        self.assertEqual(len(neutral), 1)
        self.assertEqual(neutral[0]["end"], completed["completed_at"])

    def test_snapshot_is_not_overwritten_by_later_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            first = snapshot(path, lambda: [{"id": 1}])
            second = snapshot(path, lambda: self.fail("checkpoint should not refetch"))
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
