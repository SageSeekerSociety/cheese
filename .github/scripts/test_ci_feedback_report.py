import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "ci_feedback_report", HERE / "ci-feedback-report.py"
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class ApiReadTests(unittest.TestCase):
    def test_transient_failure_repeats_read_then_returns_complete_pages(self):
        error = report.subprocess.CalledProcessError(
            1, "gh", stderr="gh: Server Error (HTTP 502)"
        )
        with (
            mock.patch.object(
                report.subprocess, "check_output", side_effect=[error, '[{"jobs": []}]']
            ) as call,
            mock.patch.object(report.time, "sleep"),
            contextlib.redirect_stderr(io.StringIO()) as log,
        ):
            self.assertEqual(
                report.gh_pages("jobs", {"per_page": "100"}), [{"jobs": []}]
            )
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args_list[0], call.call_args_list[1])
        self.assertIn("HTTP 502", log.getvalue())

    def test_persistent_server_failure_stops_after_three_reads(self):
        error = report.subprocess.CalledProcessError(
            1, "gh", stderr="gh: Server Error (HTTP 503)"
        )
        with (
            mock.patch.object(
                report.subprocess, "check_output", side_effect=error
            ) as call,
            mock.patch.object(report.time, "sleep"),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(report.subprocess.CalledProcessError),
        ):
            report.gh_pages("jobs")
        self.assertEqual(call.call_count, 3)

    def test_authorization_failure_does_not_retry(self):
        error = report.subprocess.CalledProcessError(
            1, "gh", stderr="gh: Bad credentials (HTTP 401)"
        )
        with (
            mock.patch.object(
                report.subprocess, "check_output", side_effect=error
            ) as call,
            mock.patch.object(report.time, "sleep") as sleep,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(report.subprocess.CalledProcessError),
        ):
            report.gh_pages("jobs")
        self.assertEqual(call.call_count, 1)
        sleep.assert_not_called()


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
    def evidence_record(self, workflow="required-ci.yml", run_id=1, latest=1):
        return report.analyse_attempt(
            run(run_id, latest=latest),
            1,
            [job("e2e / e2e")]
            if workflow == "required-ci.yml"
            else [job("acceptance"), job("private-chat")],
            workflow,
            "pull_request",
        )

    def receipt(self, suite="e2e", **changes):
        value = dict(
            schema_version=1,
            run_id=1,
            run_attempt=1,
            head_sha="sha-1",
            suite=suite,
            clean=True,
            reason="all_passed",
            tests=31,
            retries=0,
            skipped=0,
        )
        value.update(changes)
        return value

    def archive(self, value):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as bundle:
            bundle.writestr("evidence.json", json.dumps(value))
        return output.getvalue()

    def inspect_receipts(self, record, receipts, *, expired=False):
        artifacts = [
            {
                "id": index,
                "name": f"ci-test-evidence-1-1-{value['suite']}",
                "expired": expired,
            }
            for index, value in enumerate(receipts, 1)
        ]
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch.object(
                report, "gh_pages", return_value=[{"artifacts": artifacts}]
            ),
            mock.patch.object(
                report.subprocess,
                "check_output",
                side_effect=[self.archive(value) for value in receipts],
            ),
        ):
            report.collect_evidence([record], "owner/repo", pathlib.Path(folder), "now")
        return record["test_evidence"]

    def test_successful_workflow_with_retried_test_is_not_clean(self):
        item = self.evidence_record()
        evidence = self.inspect_receipts(
            item, [self.receipt(clean=False, retries=1, reason="test_retried")]
        )
        self.assertEqual(evidence["status"], "not_clean")
        summary = report.summarize([item])["required-ci.yml:pull_request"]
        self.assertEqual(summary["cohort_tail_consecutive_first_attempt_successes"], 1)
        self.assertEqual(summary["cohort_tail_consecutive_clean_first_attempts"], 0)

    def test_remote_acceptance_needs_both_job_receipts(self):
        item = self.evidence_record("remote-execution.yml")
        self.assertEqual(
            self.inspect_receipts(item, [self.receipt("remote-acceptance")])["status"],
            "unknown",
        )
        self.assertEqual(
            self.inspect_receipts(
                item, [self.receipt("remote-acceptance"), self.receipt("private-chat")]
            )["status"],
            "clean",
        )

    def test_required_remote_jobs_need_both_receipts_even_without_e2e(self):
        for names in (
            ("remote / acceptance",),
            ("remote / private-chat",),
            ("remote / acceptance", "remote / private-chat"),
        ):
            with self.subTest(names=names):
                item = self.evidence_record()
                item["executed_job_names"] = list(names)
                self.assertEqual(
                    report.evidence_suites(item), ["remote-acceptance", "private-chat"]
                )
                self.assertEqual(
                    self.inspect_receipts(item, [self.receipt("remote-acceptance")])[
                        "status"
                    ],
                    "unknown",
                )
                self.assertEqual(
                    self.inspect_receipts(
                        item,
                        [
                            self.receipt("remote-acceptance"),
                            self.receipt("private-chat"),
                        ],
                    )["status"],
                    "clean",
                )

    def test_required_e2e_receipt_cannot_hide_missing_remote_receipts(self):
        item = self.evidence_record()
        item["executed_job_names"] += ["remote / acceptance", "remote / private-chat"]
        self.assertEqual(
            self.inspect_receipts(item, [self.receipt()])["status"], "unknown"
        )
        self.assertEqual(
            self.inspect_receipts(
                item,
                [
                    self.receipt(),
                    self.receipt("remote-acceptance"),
                    self.receipt("private-chat"),
                ],
            )["status"],
            "clean",
        )

    def test_invalid_receipts_cannot_establish_clean_run(self):
        for changes in (
            {"head_sha": "other"},
            {"run_attempt": 2},
            {"run_id": 2},
            {"schema_version": True},
            {"tests": 0},
            {"retries": 1},
            {"skipped": 1},
            {"tests": True},
            {"clean": "true"},
        ):
            with self.subTest(changes=changes):
                evidence = self.inspect_receipts(
                    self.evidence_record(), [self.receipt(**changes)]
                )
                self.assertEqual(evidence["status"], "unknown")

    def test_missing_expired_and_duplicate_artifacts_are_unknown(self):
        for receipts, expired in (
            ([], False),
            ([self.receipt()], True),
            ([self.receipt(), self.receipt()], False),
        ):
            with self.subTest(receipts=receipts, expired=expired):
                self.assertEqual(
                    self.inspect_receipts(
                        self.evidence_record(), receipts, expired=expired
                    )["status"],
                    "unknown",
                )

    def test_workflow_rerun_is_not_clean_even_with_successful_receipt(self):
        self.assertEqual(
            self.inspect_receipts(self.evidence_record(latest=2), [self.receipt()])[
                "status"
            ],
            "not_clean",
        )

    def test_artifact_api_failure_is_unknown_not_clean(self):
        item = self.evidence_record()
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch.object(
                report,
                "gh_pages",
                side_effect=report.subprocess.CalledProcessError(1, "gh"),
            ),
        ):
            report.collect_evidence([item], "owner/repo", pathlib.Path(folder), "now")
        self.assertEqual(item["test_evidence"]["status"], "unknown")

    def test_clean_streak_excludes_light_selections_but_stops_at_unknown(self):
        records = []
        for index, status in enumerate(
            ("clean", "unknown", "clean", "not_applicable", "clean"), 1
        ):
            item = self.evidence_record(run_id=index)
            item["test_evidence"] = {"status": status}
            records.append(item)
        summary = report.summarize(records)["required-ci.yml:pull_request"]
        self.assertEqual(summary["cohort_tail_consecutive_clean_first_attempts"], 2)
        self.assertEqual(
            summary["first_attempt_test_evidence"],
            dict(clean=3, unknown=1, not_clean=0, not_applicable=1),
        )

    def test_failed_or_cancelled_scope_interrupts_clean_streak(self):
        for conclusion in ("failure", "cancelled"):
            with self.subTest(conclusion=conclusion):
                first = self.evidence_record(run_id=1)
                last = self.evidence_record(run_id=3)
                first["test_evidence"] = last["test_evidence"] = {"status": "clean"}
                middle = report.analyse_attempt(
                    run(2, conclusion=conclusion),
                    1,
                    [job("scope", conclusion), job("e2e / e2e", "skipped")],
                    "required-ci.yml",
                    "pull_request",
                )
                with tempfile.TemporaryDirectory() as folder:
                    report.collect_evidence(
                        [middle], "owner/repo", pathlib.Path(folder), "now"
                    )
                result = report.summarize([first, middle, last])[
                    "required-ci.yml:pull_request"
                ]
                self.assertEqual(
                    result["cohort_tail_consecutive_clean_first_attempts"], 1
                )

    def test_experiment_pushes_cannot_satisfy_main_acceptance_streak(self):
        records = []
        for index in range(1, 22):
            source = run(index)
            source["head_branch"] = (
                "main" if index < 20 else "experiment/remote-execution"
            )
            item = report.analyse_attempt(
                source,
                1,
                [job("acceptance"), job("private-chat")],
                "remote-execution.yml",
                "push",
            )
            item["test_evidence"] = {"status": "clean"}
            records.append(item)
        summary = report.summarize(records)
        self.assertEqual(
            summary["remote-execution.yml:push:main"][
                "cohort_tail_consecutive_clean_first_attempts"
            ],
            19,
        )
        self.assertEqual(
            summary["remote-execution.yml:push:experiment/remote-execution"][
                "unique_run_count"
            ],
            2,
        )
        records[-1]["head_branch"] = None
        self.assertEqual(
            report.summarize(records)["remote-execution.yml:push:(unknown)"][
                "unique_run_count"
            ],
            1,
        )

    def test_standalone_e2e_requires_its_own_receipt(self):
        item = report.analyse_attempt(run(), 1, [job("e2e")], "e2e.yml", "push")
        self.inspect_receipts(item, [self.receipt()])
        self.assertEqual(item["test_evidence"]["status"], "clean")
        skipped = report.analyse_attempt(
            run(), 1, [job("scope"), job("e2e", "skipped")], "e2e.yml", "push"
        )
        self.inspect_receipts(skipped, [])
        self.assertEqual(skipped["test_evidence"]["status"], "unknown")

    def test_downloaded_archive_requires_only_the_normalized_receipt(self):
        for name in ("../evidence.json", "unexpected.json"):
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as bundle:
                bundle.writestr(name, json.dumps(self.receipt()))
            with self.assertRaises(ValueError):
                report.evidence_json(output.getvalue())

    def test_evidence_is_rechecked_after_delayed_upload(self):
        item = self.evidence_record()
        artifact = {"id": 1, "name": "ci-test-evidence-1-1-e2e"}
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(
                report, "gh_pages", return_value=[{"artifacts": []}]
            ):
                report.collect_evidence(
                    [item], "owner/repo", pathlib.Path(folder), "first"
                )
            self.assertEqual(item["test_evidence"]["status"], "unknown")
            with (
                mock.patch.object(
                    report, "gh_pages", return_value=[{"artifacts": [artifact]}]
                ),
                mock.patch.object(
                    report.subprocess,
                    "check_output",
                    return_value=self.archive(self.receipt()),
                ),
            ):
                report.collect_evidence(
                    [item], "owner/repo", pathlib.Path(folder), "second"
                )
            self.assertEqual(item["test_evidence"]["status"], "clean")

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
        summary = report.summarize([first, failed, rerun])[
            "required-ci.yml:pull_request"
        ]
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
        self.assertEqual(summary["cohort_tail_consecutive_first_attempt_successes"], 0)

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
