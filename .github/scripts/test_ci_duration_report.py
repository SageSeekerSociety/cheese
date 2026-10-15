"""Behavioural checks for the Required CI duration and tree-identity report.

The tool itself calls `gh api` and needs a clone holding the pinned commits;
these fake the GitHub side and use real git objects for the merge, so the rules
are asserted without a network:

  * a skipped job is not a measurement — neither a skipped caller (whose span is
    its callee's plus queueing) nor a skipped product job;
  * a job whose span is <= 0 (what GitHub reports for a reusable-workflow caller
    of a tier that did not run) fails the run instead of entering a percentile;
  * the headroom ratio is the job's own `timeout-minutes`, not a guess;
  * a merge_group run tested the same tree as its pull_request run exactly when
    it built on the same base — and a pair that does not reconstruct cleanly is
    excluded rather than counted as a different tree.

It lives here, not beside the tool, because repo-guards' "CI helper tests" step
collects every test_*.py in this directory and nothing collects scripts/.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import subprocess
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

    def test_headroom_is_measured_against_the_jobs_own_timeout(self):
        code, out, err = self.run_jobs(
            [job("e2e / e2e", "2026-10-15T00:00:00Z", "2026-10-15T00:10:00Z")]
        )
        self.assertEqual(code, 0, out + err)
        # e2e's cap is 25 minutes and the run took 10, so 2.5x is left.
        self.assertIn("2.50x", out)
        self.assertIn("max 600s of 1500s", out)


class TreeModeTests(unittest.TestCase):
    """The queue exists to test merge(base, head) — asserted against real git."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@cheese.local")
        self.git("config", "user.name", "test")
        (self.repo / "shared.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "shared.txt")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.head = self.branch("head", {"added.txt": "head\n"})

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout

    def branch(self, name: str, files: dict[str, str], base: str | None = None) -> str:
        """A branch off `base` that writes `files`; returns its head sha."""
        self.git("checkout", "-q", "-b", name, base or self.base)
        for path, content in files.items():
            (self.repo / path).write_text(content, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", name)
        self.git("checkout", "-q", "main")
        return self.git("rev-parse", name).strip()

    def merged_tree(self, *revisions: str) -> str:
        return self.git("merge-tree", "--write-tree", *revisions).split()[0].strip()

    def run_trees(self, rows: list[dict], queue_tree: str) -> tuple[int, str, str]:
        trees = self.tmp / "trees.json"
        trees.write_text(json.dumps({"rows": rows}), encoding="utf-8")
        args = argparse.Namespace(trees=str(trees), repo=str(self.repo))
        with mock.patch.object(
            report, "_gh", lambda path: {"commit": {"tree": {"sha": queue_tree}}}
        ):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = report.mode_trees(args)
        return code, out.getvalue(), err.getvalue()

    def row(self, pr: int, base: str, queue_parent: str) -> dict:
        return {
            "pr": pr,
            "base": base,
            "head": self.head,
            "mg_head": self.head,
            "mg_parent": queue_parent,
        }

    def test_a_queue_run_on_the_same_base_tested_the_same_tree(self):
        code, out, err = self.run_trees(
            [self.row(1, self.base, self.base)], self.merged_tree(self.base, self.head)
        )
        self.assertEqual(code, 0, out + err)
        self.assertIn("1/1 merge_group runs tested the SAME tree", out)
        self.assertNotIn("INVARIANT BROKEN", out)

    def test_a_queue_run_that_tested_another_tree_is_flagged(self):
        # Same base, different tree: the queue cannot have built merge(base, head).
        code, out, err = self.run_trees(
            [self.row(2, self.base, self.base)], self.git("rev-parse", "main^{tree}").strip()
        )
        self.assertEqual(code, 0, out + err)
        self.assertIn("0/1 merge_group runs tested the SAME tree", out)
        self.assertIn("INVARIANT BROKEN", out)

    def test_a_queue_run_on_a_moved_base_differs_and_is_not_flagged(self):
        # main advanced before the queue built: a different tree is expected, so
        # there is nothing broken about it.
        moved = self.branch("moved", {"moved.txt": "moved\n"})
        code, out, err = self.run_trees(
            [self.row(3, self.base, moved)], self.merged_tree(moved, self.head)
        )
        self.assertEqual(code, 0, out + err)
        self.assertIn("0/1 merge_group runs tested the SAME tree", out)
        self.assertNotIn("INVARIANT BROKEN", out)

    def test_a_pair_that_does_not_reconstruct_is_excluded_not_counted(self):
        # Two siblings off the same base editing the same line: the queue could
        # not have built this pair, so it has no tree to compare against.
        left = self.branch("left", {"shared.txt": "left\n"})
        right = self.branch("right", {"shared.txt": "right\n"})
        code, out, err = self.run_trees([self.row(4, left, left) | {"head": right}], "0" * 40)
        self.assertEqual(code, 1)
        self.assertIn("does not reconstruct cleanly", out)
        self.assertIn("Excluded 1 of 1 row(s)", out)
        self.assertIn("nothing was measured", out)
        self.assertNotIn("/1 merge_group runs tested the SAME tree", out)
        # The tree is never fetched for a pair that has none to compare.
        self.assertNotIn("SAME tree as the PR run", out)


if __name__ == "__main__":
    unittest.main()
