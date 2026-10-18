import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# `python3 <path>` puts the script's directory on sys.path; exec_module does
# not, and rerun-failed.py imports flake_log_signatures by bare name.
sys.path.insert(0, str(Path(__file__).resolve().parent))

spec = importlib.util.spec_from_file_location(
    "rerun_failed", Path(__file__).with_name("rerun-failed.py")
)
rerun = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rerun)

REPO = "SageSeekerSociety/cheese"
ROOT = Path(__file__).resolve().parents[2]


def run(**changes):
    base = {
        "id": 42,
        "path": ".github/workflows/required-ci.yml",
        "event": "pull_request",
        "status": "completed",
        "conclusion": "failure",
        "run_attempt": 1,
        "repository": {"full_name": REPO},
    }
    return base | changes


class RerunFailedTest(unittest.TestCase):
    def test_a_failed_pull_request_run_may_be_rerun(self):
        self.assertIsNone(rerun.refusal(run(), REPO))
        self.assertIsNone(rerun.refusal(run(run_attempt=2), REPO))

    def test_only_required_ci_in_this_repository(self):
        self.assertIn("not in", rerun.refusal(run(repository={"full_name": "x/y"}), REPO))
        self.assertIn(
            "not .github", rerun.refusal(run(path=".github/workflows/deploy.yml"), REPO)
        )

    def test_a_merge_queue_run_is_not_rerun(self):
        self.assertIn("merge_group", rerun.refusal(run(event="merge_group"), REPO))

    def test_only_a_completed_failure(self):
        for status, conclusion in (
            ("in_progress", None),
            ("completed", "success"),
            ("completed", "cancelled"),
        ):
            with self.subTest(status=status, conclusion=conclusion):
                self.assertIn(
                    "not a completed failure",
                    rerun.refusal(run(status=status, conclusion=conclusion), REPO),
                )

    def test_the_third_attempt_is_the_last(self):
        self.assertIn("limit is 3", rerun.refusal(run(run_attempt=3), REPO))
        self.assertIn("limit is 3", rerun.refusal(run(run_attempt="1"), REPO))


class KnownPatternTest(unittest.TestCase):
    """known-flakes.json: what it recognises, and what it may not do."""

    def diagnosed(self, shaped):
        return rerun.refusal(run(**shaped), REPO)

    def test_every_known_case_is_refused_by_its_own_pattern(self):
        for label, shaped, pattern_id in (
            (
                "a Dependabot updater that could not reach the registry",
                {"event": "dynamic", "path": "dynamic/dependabot/dependabot-updates"},
                "dependabot-registry-502",
            ),
            (
                "the scheduled backup freshness check",
                {"path": ".github/workflows/backup-freshness.yml", "event": "schedule"},
                "db-backup-freshness",
            ),
            (
                "the scheduled dev-health probe",
                {"path": ".github/workflows/box-uptime.yml", "event": "schedule"},
                "dev-health-probe",
            ),
            (
                "the merge queue's synthetic run",
                {"event": "merge_group"},
                "merge-group-synthetic-run",
            ),
            (
                "the rerun helper's own red",
                {"path": ".github/workflows/rerun-failed.yml", "event": "repository_dispatch"},
                "rerun-workflow-red",
            ),
        ):
            with self.subTest(case=label):
                reason = self.diagnosed(shaped)
                self.assertIsNotNone(reason)
                self.assertIn(pattern_id, reason)

    def test_a_known_pattern_carries_the_handling_not_just_its_name(self):
        merge_queue = self.diagnosed({"event": "merge_group"})
        self.assertIn("重新采纳", merge_queue)
        self.assertIn("TempDir", merge_queue)
        self.assertIn("test_claude_session_records", merge_queue)

    def test_removing_a_pattern_takes_its_diagnosis_with_it(self):
        """Deliberate violation: deleting the rule is what the rule is for."""
        patterns = [
            p for p in rerun.load_patterns() if p["id"] != "db-backup-freshness"
        ]
        reason = rerun.refusal(
            run(path=".github/workflows/backup-freshness.yml", event="schedule"),
            REPO,
            patterns,
        )
        self.assertNotIn("db-backup-freshness", reason)
        self.assertNotIn("known pattern", reason)

    def test_a_pattern_matches_only_where_every_field_of_it_matches(self):
        patterns = rerun.load_patterns()
        matched = rerun.matching_pattern(
            run(path=".github/workflows/box-uptime.yml", event="schedule"), patterns
        )
        self.assertEqual(matched["id"], "dev-health-probe")
        for wrong in (
            run(path=".github/workflows/box-uptime.yml", event="pull_request"),
            run(path=".github/workflows/required-ci.yml", event="schedule"),
        ):
            with self.subTest(run=wrong):
                found = rerun.matching_pattern(wrong, patterns)
                self.assertNotEqual((found or {}).get("id"), "dev-health-probe")

    def test_a_pattern_that_allows_a_rerun_never_widens_a_gate(self):
        patterns = rerun.load_patterns()
        self.assertEqual(
            rerun.matching_pattern(run(), patterns)["id"],
            "required-ci-pull-request-intermittents",
        )
        for refused in (
            run(status="in_progress", conclusion=None),
            run(status="completed", conclusion="success"),
            run(status="completed", conclusion="cancelled"),
            run(run_attempt=3),
            run(run_attempt="1"),
        ):
            with self.subTest(run=refused):
                self.assertIsNotNone(rerun.refusal(refused, REPO))

    def test_a_pattern_that_forbids_a_rerun_refuses_a_run_the_gates_allow(self):
        patterns = [
            {
                "id": "synthetic",
                "title": "synthetic",
                "match": {"workflow": [".github/workflows/required-ci.yml"]},
                "rerun": False,
                "action": "do not rerun this one",
                "symptoms": ["synthetic"],
            }
        ]
        self.assertIn("synthetic", rerun.refusal(run(), REPO, patterns))
        self.assertIsNone(rerun.refusal(run(), REPO, []))

    def test_an_unknown_red_is_not_called_a_flake(self):
        reason = rerun.refusal(run(path=".github/workflows/deploy.yml"), REPO)
        self.assertIn("not .github/workflows/required-ci.yml", reason)
        self.assertNotIn("known pattern", reason)

    def test_every_workflow_a_pattern_names_is_a_file_in_this_repository(self):
        for pattern in rerun.load_patterns():
            for workflow in pattern["match"].get("workflow", []):
                with self.subTest(pattern=pattern["id"], workflow=workflow):
                    self.assertTrue((ROOT / workflow).is_file(), workflow)

    def test_the_configuration_is_rejected_where_a_pattern_is_written_wrong(self):
        document = json.loads(rerun.PATTERNS.read_text(encoding="utf-8"))
        for damage in (
            {"match": {"jobs": ["integration"]}},
            {"match": {}},
            {"match": {"event": []}},
            {"match": {"event": [""]}},
            {"match": {"event": [" "]}},
            {"match": {"workflow": [" .github/workflows/backup-freshness.yml "]}},
            {"rerun": "yes"},
            {"action": ""},
            {"symptoms": []},
            {"id": rerun.load_patterns()[1]["id"]},
        ):
            with self.subTest(damage=damage):
                broken = json.loads(json.dumps(document))
                broken["patterns"][0].update(damage)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "known-flakes.json"
                    path.write_text(json.dumps(broken), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        rerun.load_patterns(path)

    def test_the_workflow_step_refuses_one_run_and_reruns_the_other(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.json"
            argv = list(sys.argv)
            self.addCleanup(lambda: sys.argv.__setitem__(slice(None), argv))
            for shaped, code, marker in (
                (run(event="merge_group"), 1, "::error::"),
                (run(), 0, "required-ci-pull-request-intermittents"),
            ):
                with self.subTest(run=shaped):
                    path.write_text(json.dumps(shaped), encoding="utf-8")
                    sys.argv[:] = ["rerun-failed.py", str(path), REPO]
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        self.assertEqual(rerun.main(), code)
                    self.assertIn(marker, out.getvalue())


class PushToMainTest(unittest.TestCase):
    """The push route exists for main only, and only when the log is a flake."""

    FLAKE_LOG = (
        "backend\tRun backend tests\t2026-10-18T00:00:00Z\n"
        "Error: read ECONNRESET\n"
    )

    def push(self, **changes):
        return run(event="push", head_branch="main", **changes)

    def test_a_push_to_main_with_a_known_flake_log_may_be_rerun(self):
        self.assertIsNone(
            rerun.refusal(self.push(), REPO, failed_log=self.FLAKE_LOG)
        )

    def test_a_push_to_main_with_a_real_failure_is_refused(self):
        reason = rerun.refusal(
            self.push(), REPO, failed_log="AssertionError: 'open' != 'closed'"
        )
        self.assertIn("no known flake signature was found in the steps", reason)

    def test_a_push_to_main_without_a_readable_log_is_refused(self):
        reason = rerun.refusal(self.push(), REPO, failed_log=None)
        self.assertIn("could not be read", reason)

    def test_a_push_to_another_branch_is_refused_even_with_a_flake_log(self):
        for branch in ("task/3560a698", None):
            with self.subTest(branch=branch):
                reason = rerun.refusal(
                    run(event="push", head_branch=branch),
                    REPO,
                    failed_log=self.FLAKE_LOG,
                )
                self.assertIn("not by main", reason)

    def test_the_state_and_attempt_gates_apply_to_a_push_too(self):
        for shaped, marker in (
            (self.push(run_attempt=3), "limit is 3"),
            (self.push(run_attempt="1"), "limit is 3"),
            (self.push(conclusion="success"), "not a completed failure"),
        ):
            with self.subTest(run=shaped):
                reason = rerun.refusal(
                    shaped, REPO, failed_log=self.FLAKE_LOG
                )
                self.assertIn(marker, reason)

    def test_a_pull_request_run_keeps_its_old_behaviour(self):
        """The log gate is the push route's: PR reruns still are standard."""
        self.assertIsNone(
            rerun.refusal(run(), REPO, failed_log="AssertionError: 1 != 2")
        )

    def test_the_log_is_read_only_where_it_decides_something(self):
        """Every other run is decided without it, and a gh call is not free."""
        self.assertTrue(rerun.needs_failed_log(self.push(), REPO))
        for shaped, repository in (
            (run(), REPO),
            (self.push(run_attempt=3), REPO),
            (run(event="push", head_branch="task/1"), REPO),
            (self.push(repository={"full_name": "x/y"}), REPO),
            (self.push(), "x/y"),
        ):
            with self.subTest(run=shaped, repository=repository):
                self.assertFalse(rerun.needs_failed_log(shaped, repository))

    def test_a_refused_push_run_reports_its_refusal_not_a_log_gate(self):
        """The attempt limit is named, rather than the log that was not read."""
        reason = rerun.refusal(self.push(run_attempt=3), REPO, failed_log=None)
        self.assertIn("limit is 3", reason)
        self.assertNotIn("log", reason)


class LogSignatureTest(unittest.TestCase):
    """flake_log_signatures.py: what it recognises, and what it must not."""

    # One sample per alternative, so deleting any alternation reddens a test.
    SAMPLES = (
        ("econnreset", "read ECONNRESET"),
        ("econnreset", "write ECONNRESET"),
        ("econnreset", "connect ECONNRESET"),
        (
            "tls-connect-error",
            "curl: (35) TLS connect error: error:0A000126:SSL routines",
        ),
        ("http-403-rate-limit", "HTTP 403: API rate limit exceeded for installation"),
        ("http-403-rate-limit", "gh: API rate limit exceeded (HTTP 403)"),
        ("http-403-rate-limit", "gh: API rate limit exceeded for installation ID 1."),
        ("http-403-rate-limit", "API rate limit exceeded - HTTP 403"),
        ("runner-lost", "runner lost communication with the host"),
        ("runner-lost", "lost communication with the server"),
        ("runner-lost", "The runner has received a shutdown signal."),
        ("setup-uv-timeout", "Failed to download uv-0.5.0: operation timed out"),
        ("setup-uv-timeout", "uv install hit a timeout"),
        (
            "setup-uv-timeout",
            "Request timeout: /astral-sh/uv/releases/download/0.5.0/uv.tar.gz",
        ),
        ("setup-uv-timeout", "connect ETIMEDOUT /astral-sh/uv/releases/0.5.0"),
        ("setup-uv-timeout", "uv: ETIMEDOUT"),
    )

    def test_every_signature_matches_its_own_sample(self):
        for signature_id, sample in self.SAMPLES:
            with self.subTest(signature=signature_id):
                matched = rerun.flake_log_signature(sample)
                self.assertEqual((matched or {}).get("id"), signature_id)

    def test_matching_ignores_case(self):
        self.assertEqual(
            rerun.flake_log_signature("read econnreset")["id"], "econnreset"
        )

    # Shapes captured from `gh run view --log-failed`: job, step, timestamp,
    # then the line. The report quotes what the tests expect, so a signature
    # found in it would be the test's own text, not an observed flake.
    PYTEST_REPORT = "\n".join(
        (
            "backend\tRun backend tests\t2026-10-18T09:06:04.3675847Z"
            " =========================== FAILURES ============================",
            "backend\tRun backend tests\t2026-10-18T09:06:04.3675847Z"
            " ______ test_a_passing_failure_resumes[API Error: Connection"
            " dropped (ECONNRESET)（server_error）-unreachable] ______",
            "backend\tRun backend tests\t2026-10-18T09:06:04.3675847Z"
            " tests/unit/test_resumable_failures.py:64: in"
            " test_a_passing_failure_resumes",
            "backend\tRun backend tests\t2026-10-18T09:06:04.3675847Z"
            '     assert classify(message) == "API Error: read ECONNRESET"',
            "backend\tRun backend tests\t2026-10-18T09:06:04.3675847Z"
            " >       assert classify(message) == expected",
            "backend\tRun backend tests\t2026-10-18T09:06:04.3675847Z"
            " E       AssertionError: assert 'transient' == 'unreachable'",
            "backend\tRun backend tests\t2026-10-18T09:06:04.3675847Z"
            ' >       respond(403, json={"message": "API rate limit exceeded'
            ' for installation ID 1."})',
            "backend\tRun backend tests\t2026-10-18T09:06:04.3675847Z"
            " ==================== short test summary info ====================",
            "backend\tRun backend tests\t2026-10-18T09:06:04.3675847Z"
            " FAILED tests/unit/test_resumable_failures.py::test_a_passing_"
            "failure_resumes[API Error: Connection dropped (ECONNRESET)"
            "（server_error）-unreachable] - AssertionError",
            "",
        )
    )

    def test_an_ordinary_failure_matches_no_signature(self):
        for log in (
            "AssertionError: 'open' != 'closed'",
            "E   assert 3 == 4",
            "FAILED tests/unit/test_rate_limit.py::test_the_limiter_counts",
            # Bare tokens outside a report must not match: these are the two
            # strings the narrowed signatures deliberately stopped accepting.
            "API Error: Connection dropped (ECONNRESET)（server_error）",
            "API rate limit exceeded for installation ID 1.",
            '        json={"message": "API rate limit exceeded for installation ID 1."},',
            "AssertionError: read ECONNRESET",
            "Failed to download uv: HTTP 404 Not Found",
            "uv: checksum mismatch",
            self.PYTEST_REPORT,
            "",
        ):
            with self.subTest(log=log):
                self.assertIsNone(rerun.flake_log_signature(log))

    def test_an_indented_line_is_not_flake_evidence(self):
        """pytest and unittest reprint test source indented; a tool writes at 0."""
        for log in (
            "    read ECONNRESET",
            "backend\tRun tests\t2026-10-18T09:06:04Z     read ECONNRESET",
        ):
            with self.subTest(log=log):
                self.assertIsNone(rerun.flake_log_signature(log))

    def test_a_captured_block_does_not_swallow_a_later_step(self):
        log = "\n".join(
            (
                "backend\tRun tests\t2026-10-18T09:06:04Z"
                " ----------------------------- Captured stdout call"
                " -----------------------------",
                "backend\tRun tests\t2026-10-18T09:06:04Z"
                " Error: read ECONNRESET",
                "backend\tInstall dependencies\t2026-10-18T09:07:00Z"
                " gh: API rate limit exceeded (HTTP 403)",
            )
        )
        self.assertEqual(rerun.flake_log_signature(log)["id"], "http-403-rate-limit")

    def test_a_flake_line_beside_a_test_report_still_matches(self):
        log = "\n".join(
            (
                "backend\tInstall dependencies\t2026-10-18T09:06:04.3675847Z"
                " Error: read ECONNRESET",
                self.PYTEST_REPORT,
            )
        )
        self.assertEqual(rerun.flake_log_signature(log)["id"], "econnreset")

    # Every banner a report can start at, alone, with the test text right under
    # it at column 0. One row per name, because dropping a name from the
    # alternation is otherwise invisible: a passing run prints the warnings and
    # durations banners too, and a test id in either can carry a signature.
    BANNERS = (
        "=============================== FAILURES ===============================",
        "================================ ERRORS ================================",
        "======================== short test summary info ======================",
        "=========================== warnings summary ==========================",
        "===================== slowest 10 durations =====================",
        "=============== 2 failed, 8 passed, 1 warning in 3.04s ===============",
        "================= 1 error in 0.05s =================",
        "================= 3 skipped in 0.10s =================",
    )
    UNICODE_BANNERS = (
        # vitest, one row per report kind: each is a separate alternative in
        # `_REPORT_START`, and dropping one is invisible unless a row pins it.
        "⎯⎯⎯⎯⎯⎯⎯ Failed Tests 2 ⎯⎯⎯⎯⎯⎯⎯",
        "⎯⎯⎯⎯⎯⎯⎯ Failed Suites 1 ⎯⎯⎯⎯⎯⎯⎯",
        "⎯⎯⎯⎯⎯ Unhandled Errors ⎯⎯⎯⎯⎯",
        "⎯⎯⎯⎯⎯ Unhandled Rejection ⎯⎯⎯⎯⎯",
    )

    def test_each_report_banner_starts_the_bound_on_its_own(self):
        flake = "read ECONNRESET"
        for banner in self.BANNERS + self.UNICODE_BANNERS:
            with self.subTest(banner=banner):
                self.assertIsNone(rerun.flake_log_signature(f"{banner}\n{flake}"))

    def test_a_test_step_is_excluded_whole(self):
        """Whatever a runner echoes live is test text: console output before
        the report, failed suites, unhandled rejections, TAP lines."""
        step = "frontend\tUnit tests\t2026-10-18T09:06:04Z "
        for log in (
            # vitest: a passing/failing test's console output, then the report.
            "\n".join((
                step + " RUN  v5.0.3 /home/runner/work/cheese/cheese/frontend",
                step + "stderr | src/x.spec.ts > logs then fails",
                step + "Error: read ECONNRESET",
                step + "⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯",
                step + " FAIL  src/x.spec.ts > x > fails",
                step + "AssertionError: expected 1 to be 2",
            )),
            # vitest: a suite that throws at import time.
            "\n".join((
                step + " RUN  v5.0.3 /home/runner/work/cheese/cheese/frontend",
                step + "⎯⎯⎯⎯⎯ Failed Suites 1 ⎯⎯⎯⎯⎯",
                step + "Error: read ECONNRESET while connecting to the fixture",
            )),
            # vitest: an unhandled rejection from a passing test.
            "\n".join((
                step + " RUN  v5.0.3 /home/runner/work/cheese/cheese/frontend",
                step + "⎯⎯⎯ Unhandled Errors ⎯⎯⎯",
                step + "Error: Error: read ECONNRESET",
            )),
            # node --test, whose TAP stream marks the session.
            "\n".join((
                step + "TAP version 13",
                step + "# Error: read ECONNRESET",
                step + "not ok 1 - a test",
            )),
            # go test: the session marker is the per-test RUN line.
            "\n".join((
                step + "=== RUN   TestClassify",
                step + "Error: read ECONNRESET",
                step + "--- FAIL: TestClassify (0.00s)",
            )),
            # vitest with the session banner cut off (a truncated job log).
            "\n".join((
                step + "⎯⎯⎯⎯⎯ Failed Suites 1 ⎯⎯⎯⎯⎯",
                step + "Error: read ECONNRESET while connecting to the fixture",
            )),
            # unittest buffers a test's output and prints it after its header,
            # so the report bound covers the whole of it.
            "\n".join((
                step + "FAIL: test_x (__main__.TestX)",
                step + "read ECONNRESET while reading",
            )),
            # pytest -v: the per-test line carries the parametrize id.
            "\n".join((
                step + "============================= test session starts"
                " ==============================",
                step + "tests/unit/test_x.py::test_x[API Error: read ECONNRESET]"
                " FAILED [ 50%]",
            )),
        ):
            with self.subTest(log=log.splitlines()[0]):
                self.assertIsNone(rerun.flake_log_signature(log))

    def test_playwright_marks_its_step_as_a_test_session(self):
        """The e2e job's Vite server logs a benign `[WebServer] Error: read
        ECONNRESET` on every run, so that step must be excluded whole."""
        step = "job 9\t\t2026-10-18T09:06:04Z "
        log = "\n".join((
            step + "##[group]Run bash ./run-isolated.sh --shard 1/5",
            step + "Running 49 tests using 1 worker",
            step + "[WebServer] Error: read ECONNRESET",
            step + "  1 failed",
        ))
        self.assertIsNone(rerun.flake_log_signature(log))

    def test_a_runner_lost_annotation_under_a_test_step_is_still_evidence(self):
        """The runner's message has no `##[group]`, so it inherits the test
        step's key; it must not be dropped with that step's text."""
        step = "job 9\t\t2026-10-18T09:06:04Z "
        log = "\n".join((
            step + "##[group]Run pytest tests",
            step + "========================= test session starts =========================",
            step + "##[error]The runner has received a shutdown signal. This can"
            " happen when the runner service is stopped.",
        ))
        self.assertEqual(rerun.flake_log_signature(log)["id"], "runner-lost")

    def test_an_annotation_without_a_node_id_is_not_evidence(self):
        """report_failed_cases.py also prints a JUnit tail with no `::`."""
        log = (
            "##[error]the shard wrote no JUnit results; pytest output tail:"
            " Error: read ECONNRESET"
        )
        self.assertIsNone(rerun.flake_log_signature(log))

    def test_a_runner_lost_annotation_is_still_evidence(self):
        """The runner's own message carries `##[error]`, and is a flake."""
        for text in (
            "##[error]The runner has received a shutdown signal. This can happen"
            " when the runner service is stopped.",
            "##[error]The hosted runner lost communication with the server.",
            # Required CI's push runs share the self-hosted pool (cheese-ci);
            # GitHub names the runner in this message, so the `self-hosted`
            # alternative in `_RUNNER_ANNOTATION` is what recognises it.
            "##[error]The self-hosted runner: cheese-ci lost communication with"
            " the server. Verify the machine is running and has a healthy network"
            " connection.",
            "::error::The runner has received a shutdown signal.",
        ):
            with self.subTest(text=text):
                self.assertEqual(rerun.flake_log_signature(text)["id"], "runner-lost")

    def test_a_runner_lost_phrase_reprinted_by_a_case_is_not_evidence(self):
        """An annotation reprints a case's own message, which can quote the
        runner's wording anywhere; only the runner's message starts with it."""
        for log in (
            "##[error]tests/unit/test_runner.py::test_reports_lost — "
            "AssertionError: 'The runner has received a shutdown signal.' != 'ok'",
            "::error file=backend/tests/unit/test_runner.py,line=5::"
            "test_reports_lost — the hosted runner lost communication with the server",
            "::error::the shard wrote no JUnit results; pytest output tail:"
            " The runner has received a shutdown signal.",
        ):
            with self.subTest(log=log):
                self.assertIsNone(rerun.flake_log_signature(log))

    def test_a_step_boundary_is_the_group_marker_not_the_job(self):
        """A setup failure earlier in a job must not be swallowed by a later
        test step: the per-job REST logs have no step field of their own, so
        the `##[group]` markers are what name the step."""
        step = "job 7\t\t2026-10-18T09:06:04Z "
        log = "\n".join((
            step + "##[group]Run uv sync",
            step + "Failed to download uv-0.5.0: connect ETIMEDOUT",
            step + "##[endgroup]",
            step + "##[group]Run pytest tests",
            step + "=============== test session starts ===============",
            step + "Error: read ECONNRESET",
        ))
        self.assertEqual(rerun.flake_log_signature(log)["id"], "setup-uv-timeout")

    def test_a_job_annotation_that_reprints_a_case_is_not_evidence(self):
        """report_failed_cases.py prints `##[error]<node id> — <message>`."""
        for log in (
            "##[error]tests/contract/test_x.py::test_y — AssertionError: read ECONNRESET",
            "::error file=backend/x.py::read ECONNRESET while connecting",
            "##[error]Process completed with exit code 1.",
        ):
            with self.subTest(log=log):
                self.assertIsNone(rerun.flake_log_signature(log))

    def test_a_non_test_step_marker_does_not_exclude_the_whole_step(self):
        """`ERROR: ` and a traceback alone are not a test session."""
        log = "\n".join((
            "backend\tInstall dependencies\t2026-10-18T09:06:04Z Error: read ECONNRESET",
            "backend\tInstall dependencies\t2026-10-18T09:06:05Z ERROR: sync failed",
        ))
        self.assertEqual(rerun.flake_log_signature(log)["id"], "econnreset")

    def test_osc_sequences_are_stripped_before_matching(self):
        esc = "\x1b"
        log = "\n".join((
            "frontend\tUnit tests\t2026-10-18T09:06:04Z "
            + esc + "]8;;https://example/" + esc + "\\ RUN  v5.0.3 /app",
            "frontend\tUnit tests\t2026-10-18T09:06:04Z Error: read ECONNRESET",
        ))
        self.assertIsNone(rerun.flake_log_signature(log))

    def test_ansi_colour_is_stripped_before_matching(self):
        esc = "\x1b["
        log = "\n".join((
            "frontend\tUnit tests\t2026-10-18T09:06:04Z "
            + esc + "1m" + esc + "30m" + esc + "46m RUN " + esc + "49m" + esc + "39m",
            "frontend\tUnit tests\t2026-10-18T09:06:04Z "
            + esc + "31m⎯⎯⎯⎯" + esc + "39m" + esc + "41m Failed Tests 1 " + esc + "49m",
            "frontend\tUnit tests\t2026-10-18T09:06:04Z Error: read ECONNRESET",
        ))
        self.assertIsNone(rerun.flake_log_signature(log))
        # A real flake in another step is still read, colour or not.
        other = ("backend\tInstall dependencies\t2026-10-18T09:07:00Z "
                 + esc + "31mError: read ECONNRESET" + esc + "39m")
        self.assertEqual(rerun.flake_log_signature(log + "\n" + other)["id"], "econnreset")

    def test_each_report_marker_starts_the_bound_on_its_own(self):
        flake = "read ECONNRESET"
        for marker in (
            "--- FAIL: TestClassify (0.00s)",
            "panic: runtime error: index out of range",
            "Traceback (most recent call last):",
            "FAIL: test_x (__main__.TestX)",
            "ERROR: test_x (__main__.TestX)",
            " FAIL  src/x.spec.ts > x > fails",
            "______ test_x ______",
            "----------------------------- Captured stdout call -----------------------------",
        ):
            with self.subTest(marker=marker):
                self.assertIsNone(rerun.flake_log_signature(f"{marker}\n{flake}"))

    def test_every_pytest_report_shape_is_skipped(self):
        """Each shape is a separate row so deleting one branch reddens a test."""
        flake = "read ECONNRESET"
        blocks = {
            "failures banner": (
                "=================== FAILURES ===================\n"
                f"______ test_x[{flake}] ______\n"
                f"    {flake}"
            ),
            "errors banner": (
                "=================== ERRORS ===================\n"
                f"______ test_x[{flake}] ______\n"
                f"    {flake}"
            ),
            "test header": (
                f"__________________ test_x[{flake}] __________________\n"
                f"    {flake}"
            ),
            "short summary banner": (
                "============= short test summary info =============\n"
                f"FAILED tests/unit/test_x.py::test_x - {flake}"
            ),
            "location line (short)": f"test_x.py:64: in test_x\n    {flake}",
            "location line (auto)": f"test_x.py:64: AssertionError\n    {flake}",
            "indented source": f"test_x.py:64: in test_x\n    {flake}",
            "auto source block": (
                "__________________ test_x __________________\n"
                "\n"
                "    def test_x(self):\n"
                f'        print("Error: {flake}")\n'
                f">       assert {flake}\n"
                f"E       AssertionError: {flake}"
            ),
            "assertion detail": f"E       AssertionError: {flake}",
            "short summary line": f"FAILED tests/unit/test_x.py::test_x - {flake}",
            "introspected assertion": f"assert {flake}",
            "captured stdout": (
                "----------------------------- Captured stdout call "
                "-----------------------------\n"
                f"Error: {flake}\n"
                "----------------------------- Captured stderr call "
                "-----------------------------\n"
                f"HTTP 403: rate limit - {flake}\n"
                "=========================== short test summary info "
                "===========================\n"
                "1 failed in 0.05s"
            ),
            "captured block with an inner count line": (
                "----------------------------- Captured stdout call "
                "-----------------------------\n"
                "1 passed in 0.01s\n"
                f"upstream said: {flake} while uploading\n"
                "=========================== short test summary info "
                "===========================\n"
                "1 failed in 0.05s"
            ),
            "unittest traceback": (
                "FAIL: test_x (__main__.TestX)\n"
                "----------------------------------------------------------------------\n"
                "Traceback (most recent call last):\n"
                '  File "test_x.py", line 25, in test_x\n'
                f'    rerun.flake_log_signature("{flake}")["id"]\n'
                f"AssertionError: {flake}\n"
                "\n"
                "Ran 1 test in 0.001s\n"
                "FAILED (failures=1)"
            ),
            "error summary line": f"ERROR tests/unit/test_x.py::test_x - {flake}",
            "auto marker without a banner": f">       assert {flake}",
            "summary banner alone": (
                "============= short test summary info =============\n"
                "1 failed in 0.05s\n"
                "read ECONNRESET"
            ),
            "unittest multi-line message": (
                "FAIL: test_x (__main__.TestX)\n"
                "AssertionError: first line of explanation\n"
                "read ECONNRESET while reading"
            ),
            "traceback continuation": (
                "Traceback (most recent call last):\n"
                "read ECONNRESET while reading"
            ),
        }
        for label, block in blocks.items():
            with self.subTest(shape=label):
                self.assertIsNone(rerun.flake_log_signature(block))

    def test_a_report_ends_at_the_step_boundary(self):
        """The next step's tool output is still scanned, whatever the report left."""
        for tail in (
            ("FAIL: test_x (__main__.TestX)", "Traceback (most recent call last):"),
            (
                "=================== FAILURES ===================",
                "FAILED tests/unit/test_x.py::test_x - AssertionError",
                "============================== 1 failed in 0.05s ==============================",
            ),
        ):
            with self.subTest(tail=tail):
                log = "\n".join(
                    [
                        *(f"backend\tRun tests\t2026-10-18T09:06:04Z {line}" for line in tail),
                        "backend\tInstall dependencies\t2026-10-18T09:07:00Z"
                        " Error: read ECONNRESET",
                    ]
                )
                self.assertEqual(rerun.flake_log_signature(log)["id"], "econnreset")

    def test_a_tool_error_inside_a_reported_step_is_refused(self):
        """Deliberate bias: after a report starts, that step's text is not evidence."""
        log = "\n".join(
            (
                "backend\tRun tests\t2026-10-18T09:06:04Z"
                " =================== FAILURES ===================",
                "backend\tRun tests\t2026-10-18T09:06:04Z"
                " upstream_message = 'upstream said: read ECONNRESET'",
                "backend\tRun tests\t2026-10-18T09:06:04Z Error: read ECONNRESET",
            )
        )
        self.assertIsNone(rerun.flake_log_signature(log))

    def test_the_signatures_argument_is_honoured(self):
        custom = (
            {
                "id": "custom",
                "title": "custom",
                "pattern": r"\bboom\b",
                "action": "rerun",
            },
        )
        self.assertEqual(
            rerun.flake_log_signature("a boom happened", custom)["id"], "custom"
        )
        self.assertIsNone(rerun.flake_log_signature("a boom happened"))

    def test_the_signature_list_is_valid(self):
        rerun.validate_log_signatures()
        self.assertTrue(rerun.LOG_SIGNATURES)

    def test_a_signature_written_wrong_is_rejected(self):
        good = dict(rerun.LOG_SIGNATURES[0])
        for damage in (
            {"id": ""},
            {"title": " "},
            {"pattern": "("},
            {"action": ""},
            {"id": rerun.LOG_SIGNATURES[1]["id"]},
            {"pattern": None},
        ):
            with self.subTest(damage=damage):
                broken = [good | damage, *rerun.LOG_SIGNATURES[1:]]
                with self.assertRaises(ValueError):
                    rerun.validate_log_signatures(broken)


class RunIdEntryTest(unittest.TestCase):
    """`--run <id>` is the entry a person or the orchestrator calls directly."""

    FLAKE_LOG = PushToMainTest.FLAKE_LOG

    def call(self, arguments, run_object, log=None, rerun_failure=None):
        calls = []
        log_fetches = []

        def record(run_id, repository):
            calls.append((run_id, repository))
            return rerun_failure

        def fetch_logs(run_id, repository):
            log_fetches.append((run_id, repository))
            return log

        saved = list(sys.argv)
        self.addCleanup(lambda: sys.argv.__setitem__(slice(None), saved))
        sys.argv[:] = ["rerun-failed.py", *arguments]
        out = io.StringIO()
        with (
            mock.patch.object(rerun, "fetch_run", return_value=run_object),
            mock.patch.object(rerun, "fetch_failed_logs", side_effect=fetch_logs),
            mock.patch.object(rerun, "rerun_failed_jobs", side_effect=record),
            contextlib.redirect_stdout(out),
        ):
            code = rerun.main()
        return code, out.getvalue(), calls, log_fetches

    def test_it_fetches_the_run_and_reruns_when_the_log_is_a_flake(self):
        code, out, calls, log_fetches = self.call(
            ["--run", "42", REPO],
            run(event="push", head_branch="main"),
            log=self.FLAKE_LOG,
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [(42, REPO)])
        self.assertEqual(log_fetches, [(42, REPO)])
        self.assertIn("failed on known flake 'econnreset'", out)

    def test_dry_run_only_prints_the_decision(self):
        code, out, calls, log_fetches = self.call(
            ["--run", "42", REPO, "--dry-run"],
            run(event="push", head_branch="main"),
            log=self.FLAKE_LOG,
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertEqual(log_fetches, [(42, REPO)])
        self.assertIn("would rerun its failed jobs", out)
        self.assertNotIn("rerunning its failed jobs", out)

    def test_a_real_failure_is_refused_and_not_rerun(self):
        code, out, calls, _ = self.call(
            ["--run", "42", REPO],
            run(event="push", head_branch="main"),
            log="AssertionError: 1 != 2",
        )
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])
        self.assertIn("::error::", out)

    def test_the_workflow_saved_json_path_reads_a_push_run_s_log(self):
        """rerun-failed.yml calls the saved-JSON form, not --run."""
        run_object = run(event="push", head_branch="main")
        saved = list(sys.argv)
        self.addCleanup(lambda: sys.argv.__setitem__(slice(None), saved))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.json"
            path.write_text(json.dumps(run_object), encoding="utf-8")
            sys.argv[:] = ["rerun-failed.py", str(path), REPO]
            fetches = []

            def fetch_logs(run_id, repository):
                fetches.append((run_id, repository))
                return self.FLAKE_LOG

            out = io.StringIO()
            with (
                mock.patch.object(rerun, "fetch_failed_logs", side_effect=fetch_logs),
                mock.patch.object(rerun, "rerun_failed_jobs") as rerun_jobs,
                contextlib.redirect_stdout(out),
            ):
                self.assertEqual(rerun.main(), 0)
        self.assertEqual(fetches, [(42, REPO)])
        rerun_jobs.assert_not_called()

    def test_a_pull_request_run_never_reads_a_log(self):
        code, _, calls, log_fetches = self.call(
            ["--run", "42", REPO], run(), log=self.FLAKE_LOG
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [(42, REPO)])
        self.assertEqual(log_fetches, [])

    def test_a_run_that_could_not_be_fetched_is_an_error(self):
        saved = list(sys.argv)
        self.addCleanup(lambda: sys.argv.__setitem__(slice(None), saved))
        sys.argv[:] = ["rerun-failed.py", "--run", "42", REPO]
        with (
            mock.patch.object(rerun, "fetch_run", return_value=None),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(rerun.main(), 1)

    def test_a_run_id_that_is_not_a_number_is_a_usage_error(self):
        code, _, calls, _ = self.call(["--run", "abc", REPO], run())
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])

    def test_a_duplicated_dry_run_is_a_usage_error(self):
        code, _, calls, fetches = self.call(
            ["--run", "42", REPO, "--dry-run", "--dry-run"], run()
        )
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(fetches, [])

    def test_extra_arguments_are_a_usage_error(self):
        code, _, calls, fetches = self.call(["--run", "42", REPO, "extra"], run())
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertEqual(fetches, [])

    def test_the_rerun_command_failing_is_an_error(self):
        code, out, _, _ = self.call(
            ["--run", "42", REPO],
            run(event="push", head_branch="main"),
            log=self.FLAKE_LOG,
            rerun_failure="HTTP 403",
        )
        self.assertEqual(code, 1)
        self.assertIn("could not rerun run 42", out)

    def test_a_broken_signature_list_stops_the_entry(self):
        """A bad flake list must be named, not silently ignored."""
        saved = list(sys.argv)
        self.addCleanup(lambda: sys.argv.__setitem__(slice(None), saved))
        sys.argv[:] = ["rerun-failed.py", "--run", "42", REPO]
        out = io.StringIO()
        with (
            mock.patch.object(
                rerun,
                "validate_log_signatures",
                side_effect=ValueError("signature 'x' pattern does not compile"),
            ),
            mock.patch.object(rerun, "fetch_run") as fetch,
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(rerun.main(), 1)
        fetch.assert_not_called()
        self.assertIn("does not compile", out.getvalue())


class GhCallTest(unittest.TestCase):
    """How the helper invokes `gh`, including the log that is not UTF-8."""

    def test_the_output_is_decoded_with_replacement_not_strictly(self):
        completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with mock.patch.object(rerun.subprocess, "run", return_value=completed) as run:
            self.assertIs(rerun._gh("api", "x"), completed)
        self.assertEqual(run.call_args.kwargs["errors"], "replace")

    def test_a_decode_error_is_a_refusal_not_a_traceback(self):
        with (
            mock.patch.object(
                rerun.subprocess,
                "run",
                side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertIsNone(rerun._gh("run", "view", "1", "--log-failed"))

    def test_the_log_is_read_per_failed_job_by_rest(self):
        """`gh run view --log-failed` cannot match a reusable-workflow job name
        (`backend / contract (0/1)` here, `backend  contract (01)` there), so
        every product suite's log would be missing from its output."""
        jobs = json.dumps(
            {"jobs": [
                {"id": 7, "conclusion": "failure"},
                {"id": 8, "conclusion": "success"},
                {"id": 9, "conclusion": "failure"},
            ]}
        )
        calls = []

        def fake(argv, **kwargs):
            calls.append(tuple(argv[1:]))
            if argv[-1].startswith("repos/o/r/actions/runs/"):
                return subprocess.CompletedProcess([], 0, stdout=jobs, stderr="")
            if argv[-1] == "repos/o/r/actions/jobs/7/logs":
                return subprocess.CompletedProcess([], 0, stdout="read ECONNRESET", stderr="")
            return subprocess.CompletedProcess([], 0, stdout="other", stderr="")

        with mock.patch.object(rerun.subprocess, "run", side_effect=fake):
            log = rerun.fetch_failed_logs("42", "o/r")
        self.assertIn(("api", "repos/o/r/actions/runs/42/jobs?filter=latest&per_page=100&page=1"), calls)
        self.assertIn(("api", "repos/o/r/actions/jobs/7/logs"), calls)
        self.assertIn(("api", "repos/o/r/actions/jobs/9/logs"), calls)
        self.assertNotIn(("api", "repos/o/r/actions/jobs/8/logs"), calls)
        self.assertIsNotNone(log)
        self.assertIn("read ECONNRESET", log)
        # Each job's lines carry that job's id and an empty step field: the
        # `##[group]` markers in the text name the step, and two jobs are
        # never read as one step.
        self.assertIn("job 7\t\tread ECONNRESET", log)

    def test_the_failed_job_list_is_paged(self):
        page_one = json.dumps({"jobs": [{"id": n, "conclusion": "failure"} for n in range(100)]})
        page_two = json.dumps({"jobs": [{"id": 200, "conclusion": "failure"}]})

        def fake(argv, **kwargs):
            if argv[-1].endswith("page=1"):
                return subprocess.CompletedProcess([], 0, stdout=page_one, stderr="")
            if argv[-1].endswith("page=2"):
                return subprocess.CompletedProcess([], 0, stdout=page_two, stderr="")
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with mock.patch.object(rerun.subprocess, "run", side_effect=fake):
            self.assertEqual(len(rerun._failed_job_ids("42", "o/r")), 101)

    def test_no_readable_failed_job_log_is_unreadable_not_clean(self):
        jobs = json.dumps({"jobs": [{"id": 7, "conclusion": "failure"}]})

        def fake(argv, **kwargs):
            if argv[-1].startswith("repos/o/r/actions/runs/"):
                return subprocess.CompletedProcess([], 0, stdout=jobs, stderr="")
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with (
            mock.patch.object(rerun.subprocess, "run", side_effect=fake),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertIsNone(rerun.fetch_failed_logs("42", "o/r"))

    def test_the_rerun_command_only_reruns_failed_jobs(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(rerun.subprocess, "run", return_value=completed) as run:
            self.assertIsNone(rerun.rerun_failed_jobs("42", "o/r"))
        self.assertEqual(
            run.call_args.args[0],
            ["gh", "run", "rerun", "42", "--failed", "--repo", "o/r"],
        )

    def test_the_run_is_fetched_from_the_repository_path(self):
        completed = subprocess.CompletedProcess([], 0, stdout='{"id": 42}', stderr="")
        with mock.patch.object(rerun.subprocess, "run", return_value=completed) as run:
            self.assertEqual(rerun.fetch_run("42", "o/r"), {"id": 42})
        self.assertEqual(
            run.call_args.args[0], ["gh", "api", "repos/o/r/actions/runs/42"]
        )

    def test_an_empty_successful_log_is_unreadable_not_clean(self):
        completed = subprocess.CompletedProcess([], 0, stdout="\n", stderr="")
        with (
            mock.patch.object(rerun.subprocess, "run", return_value=completed),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertIsNone(rerun.fetch_failed_logs("42", "o/r"))

    def test_a_gh_command_that_exits_non_zero_is_refused(self):
        # Valid JSON on a failed exit: the return code alone must refuse it.
        failed = subprocess.CompletedProcess([], 1, stdout='{"id": 42}', stderr="boom")
        with (
            mock.patch.object(rerun.subprocess, "run", return_value=failed),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertIsNone(rerun.fetch_run("42", "o/r"))
            self.assertIsNone(rerun.fetch_failed_logs("42", "o/r"))
            self.assertEqual(rerun.rerun_failed_jobs("42", "o/r"), "boom")

    def test_a_run_that_is_not_json_is_unreadable(self):
        garbage = subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
        with (
            mock.patch.object(rerun.subprocess, "run", return_value=garbage),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertIsNone(rerun.fetch_run("42", "o/r"))

    def test_a_signature_entry_that_is_not_an_object_is_rejected(self):
        with self.assertRaises(ValueError):
            rerun.validate_log_signatures(["read ECONNRESET"])

    def test_a_hung_gh_is_a_refusal_not_a_traceback(self):
        with (
            mock.patch.object(
                rerun.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["gh"], 120),
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertIsNone(rerun._gh("api", "repos/o/r/actions/runs/42"))


if __name__ == "__main__":
    unittest.main()
