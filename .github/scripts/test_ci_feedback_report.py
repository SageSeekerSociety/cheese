import argparse
import contextlib
import importlib.util
import io
import pathlib
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "ci_feedback_report", HERE / "ci-feedback-report.py"
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def job(
    name,
    conclusion="success",
    created="2026-09-22T10:00:10Z",
    started="2026-09-22T10:00:20Z",
    completed="2026-09-22T10:01:00Z",
    status="completed",
):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "created_at": created,
        "started_at": started,
        "completed_at": completed,
    }


def run(run_id=1, latest=1, conclusion="success", status="completed"):
    return {
        "id": run_id,
        "run_attempt": latest,
        "latest_attempt": latest,
        "head_sha": f"sha-{run_id}",
        "created_at": "2026-09-22T10:00:00Z",
        "status": status,
        "conclusion": conclusion,
    }


class FeedbackReportTest(unittest.TestCase):
    def test_rerun_attempts_stay_separate_but_latest_success_denominator_is_one_run(
        self,
    ):
        failed = report.analyse_attempt(
            run(latest=2, conclusion="failure"),
            1,
            [job("test", "failure")],
            "required-ci.yml",
            "pull_request",
        )
        passed = report.analyse_attempt(
            run(latest=2), 2, [job("test")], "required-ci.yml", "pull_request"
        )
        summary = report.summarize([failed, passed])["required-ci.yml:pull_request"]
        self.assertEqual(summary["unique_run_count"], 1)
        self.assertEqual(summary["attempt_count"], 2)
        self.assertEqual(summary["attempt_outcomes"]["failure"], 1)
        self.assertEqual(summary["latest_attempt_outcomes"]["success"], 1)
        self.assertEqual(
            summary["successful_latest_run_created_to_all_jobs_complete_api_proxy"][
                "count"
            ],
            1,
        )

    def test_first_attempt_streak_is_not_repaired_by_a_successful_rerun(self):
        first = report.analyse_attempt(
            run(1), 1, [job("scope")], "required-ci.yml", "pull_request"
        )
        failed = report.analyse_attempt(
            run(2, latest=2, conclusion="failure"),
            1,
            [job("scope", "failure")],
            "required-ci.yml",
            "pull_request",
        )
        rerun = report.analyse_attempt(
            run(2, latest=2),
            2,
            [job("scope")],
            "required-ci.yml",
            "pull_request",
        )
        summary = report.summarize([first, failed, rerun])["required-ci.yml:pull_request"]
        self.assertEqual(summary["cohort_tail_consecutive_first_attempt_successes"], 0)
        self.assertEqual(summary["first_attempt_outcomes"]["success"], 1)
        self.assertEqual(summary["first_attempt_outcomes"]["failure"], 1)
        self.assertEqual(summary["latest_attempt_outcomes"]["success"], 2)

    def test_missing_creation_time_makes_first_attempt_streak_unknown(self):
        attempt = report.analyse_attempt(
            run(), 1, [job("scope")], "required-ci.yml", "pull_request"
        )
        attempt["run_created_at"] = None
        summary = report.summarize([attempt])["required-ci.yml:pull_request"]
        self.assertIsNone(summary["cohort_tail_consecutive_first_attempt_successes"])

    def test_latest_runs_are_grouped_by_executed_job_selection(self):
        light = report.analyse_attempt(
            run(1), 1, [job("scope"), job("guards")], "required-ci.yml", "pull_request"
        )
        full = report.analyse_attempt(
            run(2),
            1,
            [job("scope"), job("integration")],
            "required-ci.yml",
            "pull_request",
        )
        cohorts = report.summarize([light, full])["required-ci.yml:pull_request"][
            "latest_attempt_selection_cohorts"
        ]
        self.assertEqual(cohorts["guards | scope"]["run_count"], 1)
        self.assertEqual(cohorts["integration | scope"]["run_count"], 1)
        self.assertEqual(
            cohorts["integration | scope"][
                "successful_run_created_to_all_jobs_complete_api_proxy"
            ]["count"],
            1,
        )

    def test_cancelled_and_pending_are_not_failures_or_successes(self):
        cancelled = report.analyse_attempt(
            run(1, conclusion="cancelled"),
            1,
            [job("gate", "failure")],
            "required-ci.yml",
            "merge_group",
        )
        pending = report.analyse_attempt(
            run(2, conclusion=None, status="queued"),
            1,
            [job("gate", None, completed=None, status="queued")],
            "required-ci.yml",
            "merge_group",
        )
        summary = report.summarize([cancelled, pending])["required-ci.yml:merge_group"]
        self.assertEqual(summary["attempt_outcomes"]["cancelled"], 1)
        self.assertEqual(summary["attempt_outcomes"]["pending"], 1)
        self.assertEqual(
            summary["successful_latest_run_created_to_all_jobs_complete_api_proxy"][
                "count"
            ],
            0,
        )
        self.assertEqual(
            summary["cohort_tail_consecutive_first_attempt_successes"], 0
        )

    def test_parallel_jobs_use_final_completion_instead_of_added_durations(self):
        jobs = [
            job("a", started="2026-09-22T10:00:20Z", completed="2026-09-22T10:02:00Z"),
            job("b", started="2026-09-22T10:00:20Z", completed="2026-09-22T10:03:00Z"),
        ]
        attempt = report.analyse_attempt(
            run(), 1, jobs, "required-ci.yml", "pull_request"
        )
        self.assertEqual(attempt["run_created_to_final_job_completed_seconds"], 180)

    def test_missing_and_inverted_timestamps_are_reported_unknown(self):
        attempt = report.analyse_attempt(
            run(),
            1,
            [
                job(
                    "bad",
                    created="2026-09-22T10:01:00Z",
                    started="2026-09-22T09:59:00Z",
                    completed="2026-09-22T09:58:00Z",
                )
            ],
            "required-ci.yml",
            "pull_request",
        )
        self.assertEqual(attempt["job_queue_unknown_count"], 1)
        self.assertIn("run_created_to_final_job_completed", attempt["unknown_metrics"])

    def test_one_missing_job_end_invalidates_all_jobs_complete_metric(self):
        attempt = report.analyse_attempt(
            run(),
            1,
            [job("complete"), job("missing", completed=None)],
            "required-ci.yml",
            "pull_request",
        )
        self.assertIsNone(attempt["run_created_to_final_job_completed_seconds"])
        self.assertIn("run_created_to_final_job_completed", attempt["unknown_metrics"])

    def test_one_inverted_job_invalidates_mixed_all_jobs_complete_metric(self):
        attempt = report.analyse_attempt(
            run(),
            1,
            [
                job("valid"),
                job(
                    "inverted",
                    created="2026-09-22T10:02:00Z",
                    started="2026-09-22T10:03:00Z",
                    completed="2026-09-22T10:01:00Z",
                ),
            ],
            "required-ci.yml",
            "pull_request",
        )
        self.assertIsNone(attempt["run_created_to_final_job_completed_seconds"])
        self.assertIn("run_created_to_final_job_completed", attempt["unknown_metrics"])

    def test_nearest_rank_p90(self):
        self.assertEqual(report.nearest_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 100], 0.9), 9)
        self.assertEqual(report.nearest_rank([1, 100], 0.9), 100)

    def test_atomic_new_refuses_to_overwrite_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "raw.json"
            report.atomic_new(path, {"first": True})
            with self.assertRaises(FileExistsError):
                report.atomic_new(path, {"second": True})
            self.assertEqual(path.read_text(), '{\n  "first": true\n}\n')

    def test_completed_attempt_cache_is_reused_and_reclassified_when_rerun_appears(
        self,
    ):
        first_run = run(latest=1)
        rerun = run(latest=2)

        def pages(current):
            return [{"total_count": 1, "workflow_runs": [current]}]

        jobs = [{"total_count": 1, "jobs": [job("gate")]}]
        empty = [{"total_count": 0, "workflow_runs": []}]
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                repo="o/r",
                workflow=["required-ci.yml"],
                since="2026-09-22T00:00:00Z",
                until="2026-09-23T00:00:00Z",
                output_dir=pathlib.Path(directory),
            )
            with mock.patch.object(
                report,
                "gh_pages",
                side_effect=[pages(first_run), [first_run], jobs, empty],
            ) as api:
                with contextlib.redirect_stdout(io.StringIO()):
                    report.collect(args)
                self.assertEqual(api.call_count, 4)
            with mock.patch.object(
                report, "gh_pages", side_effect=[pages(rerun), [rerun], jobs, empty]
            ) as api:
                with contextlib.redirect_stdout(io.StringIO()):
                    second = report.collect(args)
                self.assertEqual(
                    api.call_count, 4
                )  # run lists plus metadata/jobs for only the new attempt
            attempts = sorted(second["attempts"], key=lambda item: item["attempt"])
            self.assertEqual([item["latest_attempt"] for item in attempts], [2, 2])
            self.assertEqual(
                second["summary"]["required-ci.yml:pull_request"]["unique_run_count"], 1
            )

    def test_interrupted_collection_resumes_after_last_completed_attempt(self):
        runs = [{"total_count": 2, "workflow_runs": [run(1), run(2)]}]
        jobs = [{"total_count": 1, "jobs": [job("gate")]}]
        empty = [{"total_count": 0, "workflow_runs": []}]
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory)
            args = argparse.Namespace(
                repo="o/r",
                workflow=["required-ci.yml"],
                since="2026-09-22T00:00:00Z",
                until="2026-09-23T00:00:00Z",
                output_dir=output,
            )
            with mock.patch.object(
                report,
                "gh_pages",
                side_effect=[runs, [run(1)], jobs, RuntimeError("API interrupted")],
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, "interrupted"):
                        report.collect(args)
            first = (output / "records" / "1-attempt-1.json").read_bytes()

            with mock.patch.object(
                report, "gh_pages", side_effect=[runs, [run(2)], jobs, empty]
            ) as api:
                with contextlib.redirect_stdout(io.StringIO()):
                    resumed = report.collect(args)
                self.assertEqual(api.call_count, 4)
            self.assertEqual(
                (output / "records" / "1-attempt-1.json").read_bytes(), first
            )
            self.assertEqual(len(resumed["attempts"]), 2)


if __name__ == "__main__":
    unittest.main()
