"""Behavioural checks for the Required CI duration report.

The tool itself calls `gh api`; these fake the GitHub side, so the rules are
asserted without a network:

  * a skipped job is not a measurement — neither a skipped caller (whose span is
    its callee's plus queueing) nor a skipped product job;
  * a job whose span is <= 0 (what GitHub reports for a reusable-workflow caller
    of a tier that did not run) fails the run instead of entering a percentile;
  * the headroom ratio is the job's own `timeout-minutes`, not a guess.

It lives here, not beside the tool, because repo-guards' "CI helper tests" step
collects every test_*.py in this directory and nothing collects scripts/.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "ci_duration_report",
    Path(__file__).resolve().parents[2] / "scripts/ci/ci-duration-report.py",
)
report = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = report
spec.loader.exec_module(report)


def job(name: str, start: str, end: str, conclusion: str = "success") -> dict:
    return {
        "name": name,
        "started_at": start,
        "completed_at": end,
        "conclusion": conclusion,
    }


class PercentileTests(unittest.TestCase):
    def test_median_of_an_even_sample_is_between_the_two_middle_ranks(self):
        self.assertEqual(report.pctl([10, 20, 30, 40], 0.5), 25.0)

    def test_a_single_value_is_every_percentile_of_itself(self):
        for p in (0.0, 0.5, 0.9, 1.0):
            self.assertEqual(report.pctl([7], p), 7)

    def test_the_ends_are_the_minimum_and_the_maximum(self):
        self.assertEqual(report.pctl([3, 1, 2], 0.0), 1)
        self.assertEqual(report.pctl([3, 1, 2], 1.0), 3)


class JobModeTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runs = Path(directory.name) / "runs.json"
        self.runs.write_text(json.dumps({"run_ids": [1]}), encoding="utf-8")
        self.args = argparse.Namespace(runs=str(self.runs), cache=None)

    def run_jobs(self, jobs: list[dict]) -> tuple[int, str, str]:
        with mock.patch.object(report, "_jobs", lambda run_id, cache: {"jobs": jobs}):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = report.mode_jobs(self.args)
        return code, out.getvalue(), err.getvalue()

    def test_a_skipped_caller_is_not_measured(self):
        code, out, err = self.run_jobs(
            [
                job("CI required", "2026-10-15T00:00:00Z", "2026-10-15T00:00:00Z", "skipped"),
                job("CI required", "2026-10-15T00:00:00Z", "2026-10-15T10:00:00Z"),
                job("backend / pure", "2026-10-15T00:00:00Z", "2026-10-15T00:03:00Z"),
            ]
        )
        self.assertEqual(code, 0, out + err)
        self.assertIn("backend / pure", out)
        self.assertNotIn("CI required", out)

    def test_a_skipped_product_job_is_dropped_without_help_from_the_name_list(self):
        code, out, err = self.run_jobs(
            [
                job("backend / integration", "2026-10-15T00:00:00Z", "2026-10-15T00:00:00Z", "skipped"),
                job("backend / pure", "2026-10-15T00:00:00Z", "2026-10-15T00:03:00Z"),
            ]
        )
        self.assertEqual(code, 0, out + err)
        self.assertIn("backend / pure", out)
        self.assertNotIn("backend / integration", out)

    def test_a_non_positive_span_fails_instead_of_being_averaged_in(self):
        code, out, err = self.run_jobs(
            [
                job("frontend / unit", "2026-10-15T00:00:10Z", "2026-10-15T00:00:00Z"),
                job("backend / pure", "2026-10-15T00:00:00Z", "2026-10-15T00:03:00Z"),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("frontend / unit=-10s", err)
        self.assertNotIn("backend / pure", out)

    def test_a_run_of_more_than_one_page_is_refused_not_truncated(self):
        # A per_page=100 page that is not the whole run would measure a subset
        # that looks exactly like the whole thing. GitHub tells us the total.
        fetched = {"total_count": 101, "jobs": [job("frontend / unit", "2026-10-01T00:00:00Z", "2026-10-01T00:10:00Z")] * 100}
        with mock.patch.object(report, "_gh", lambda path: fetched):
            with self.assertRaises(SystemExit) as caught:
                report._jobs(1, None)
        self.assertIn("more than the one page", str(caught.exception))
        self.assertIn("101", str(caught.exception))

    def test_a_run_that_fits_in_one_page_is_returned(self):
        fetched = {"total_count": 1, "jobs": [job("frontend / unit", "2026-10-01T00:00:00Z", "2026-10-01T00:10:00Z")]}
        with mock.patch.object(report, "_gh", lambda path: fetched):
            self.assertEqual(report._jobs(1, None), fetched)

    def test_headroom_is_measured_against_the_jobs_own_timeout(self):
        code, out, err = self.run_jobs(
            [job("e2e / e2e", "2026-10-15T00:00:00Z", "2026-10-15T00:10:00Z")]
        )
        self.assertEqual(code, 0, out + err)
        # e2e's cap is 25 minutes and the run took 10, so 2.5x is left.
        self.assertIn("2.50x", out)
        self.assertIn("max 600s of 1500s", out)


if __name__ == "__main__":
    unittest.main()
