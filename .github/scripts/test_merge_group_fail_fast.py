"""When a merge queue entry's `CI required` is failed early, and when never."""

import contextlib
import importlib.util
import io
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "merge_group_fail_fast", Path(__file__).with_name("merge-group-fail-fast.py")
)
fail_fast = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fail_fast
spec.loader.exec_module(fail_fast)

HEAD_SHA = "67553beb" + "0" * 32
ENV = {
    "GITHUB_REPOSITORY": "owner/repo",
    "GITHUB_RUN_ID": "100",
    "GITHUB_RUN_ATTEMPT": "1",
    "HEAD_SHA": HEAD_SHA,
}


def job(name, status="in_progress", conclusion=None, completed_at=None):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "completed_at": completed_at,
        "html_url": f"https://example.test/{name}",
    }


def done(name, conclusion, minute):
    return job(name, "completed", conclusion, f"2026-10-17T12:{minute:02d}:00Z")


class FakeGitHub:
    """Answers each poll with the next job list; records what was posted."""

    def __init__(self, polls):
        self.polls = list(polls)
        self.posted = []

    def __call__(self, *args, payload=None):
        if args[:2] == ("-X", "POST"):
            self.posted.append((args[2], payload))
            return {}
        answer = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        if isinstance(answer, Exception):
            raise answer
        return {"jobs": answer}


def watch(github, clock=None):
    ticks = iter(range(0, 10**9, fail_fast.POLL_SECONDS))
    return fail_fast.watch(
        github, ENV, sleep=lambda _: None, clock=clock or (lambda: next(ticks))
    )


class FailFastTests(unittest.TestCase):
    def test_first_failed_job_fails_the_required_check_while_others_still_run(self):
        github = FakeGitHub([
            [done("scope", "success", 1), job("backend / pure (0/2)")],
            [
                done("scope", "success", 1),
                done("guards / guards", "failure", 9),
                job("backend / pure (0/2)"),
                job("CI required", "waiting"),
            ],
        ])
        watch(github)
        self.assertEqual(len(github.posted), 1)
        path, check = github.posted[0]
        self.assertEqual(path, "repos/owner/repo/check-runs")
        self.assertEqual(check["name"], "CI required")
        self.assertEqual(check["head_sha"], HEAD_SHA)
        self.assertEqual(check["status"], "completed")
        self.assertEqual(check["conclusion"], "failure")
        self.assertIn("guards / guards", check["output"]["title"])

    def test_the_earliest_of_several_failures_is_named(self):
        github = FakeGitHub([[
            done("backend / pure (1/2)", "failure", 30),
            done("guards / guards", "timed_out", 12),
        ]])
        watch(github)
        self.assertIn("guards / guards", github.posted[0][1]["output"]["title"])

    def test_a_green_run_posts_nothing_and_stops_when_the_aggregator_starts(self):
        github = FakeGitHub([
            [done("scope", "success", 1), job("backend / pure (0/2)")],
            [
                done("scope", "success", 1),
                done("backend / pure (0/2)", "success", 20),
                job("CI required", "in_progress"),
            ],
        ])
        clock = iter([0, 1, 2])  # a third poll would exhaust it and raise
        watch(github, clock=lambda: next(clock))
        self.assertEqual(github.posted, [])

    def test_suites_all_complete_before_the_slow_ones_appear_does_not_end_the_watch(self):
        github = FakeGitHub([
            [done("scope", "success", 1), done("cli", "skipped", 1)],
            [done("scope", "success", 1), done("backend / pure (0/2)", "failure", 25)],
        ])
        watch(github)
        self.assertEqual(len(github.posted), 1)

    def test_cancelled_skipped_and_aggregator_results_are_not_a_suite_failure(self):
        github = FakeGitHub([[
            done("backend / pure (0/2)", "cancelled", 5),
            done("e2e", "skipped", 1),
            done("Fail fast", "failure", 6),
            done("CI required (draft quick)", "failure", 6),
            done("CI required", "failure", 7),
        ]])
        watch(github)
        self.assertEqual(github.posted, [])

    def test_a_failed_read_is_retried_and_never_raises(self):
        blip = subprocess.CalledProcessError(1, "gh", stderr="HTTP 502")
        github = FakeGitHub([blip, [done("guards / guards", "failure", 3)]])
        with contextlib.redirect_stdout(io.StringIO()):
            watch(github)
        self.assertEqual(len(github.posted), 1)

    def test_the_watch_ends_at_its_deadline_without_posting(self):
        github = FakeGitHub([[job("backend / pure (0/2)")]])
        clock = iter([0, fail_fast.DEADLINE_SECONDS])
        watch(github, clock=lambda: next(clock))
        self.assertEqual(github.posted, [])

    def test_main_exits_zero_whatever_goes_wrong(self):
        with unittest.mock.patch.object(fail_fast, "watch", side_effect=KeyError("x")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(fail_fast.main(), 0)
        self.assertIn("::warning::", out.getvalue())


if __name__ == "__main__":
    unittest.main()
