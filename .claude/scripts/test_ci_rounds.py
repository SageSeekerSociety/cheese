"""Offline contracts for PR-lifetime rounds and evidence, not existing metrics."""

from copy import deepcopy
from datetime import timedelta
import sys
import unittest
from unittest.mock import patch

import ci_rounds as report
import ci_rounds_collect as collect

SINCE = "2026-10-01T00:00:00Z"
UNTIL = "2026-10-15T00:00:00Z"


def check(name=report.FULL, conclusion="SUCCESS", at="2026-10-02T01:00:00Z"):
    return {"name": name, "conclusion": conclusion, "completedAt": at, "detailsUrl": "https://github.com/test/job/1"}


def run(number, sha, at, checks=None, message="feat: first change"):
    return {"id": number, "head_sha": sha, "created_at": at, "updated_at": at,
            "cohort_prs": [1], "checks": checks or [], "commit_message": message,
            "run_attempt": 1, "conclusion": "success"}


def snapshot(rows=None):
    return {"schema_version": 1, "repo": "owner/repo", "since": SINCE, "until": UNTIL,
            "observed_at": UNTIL, "timelines_complete": True,
            "prs": [{"number": 1, "created_at": SINCE, "closed_at": None,
                     "merged_at": None, "timeline": []}], "runs": rows or []}


class Rounds(unittest.TestCase):
    def test_promoting_and_rerunning_one_head_is_one_round(self):
        data = snapshot([run(1, "a", SINCE, [check(report.DRAFT)]),
                         run(2, "a", "2026-10-03T00:00:00Z", [check()])])
        data["runs"][1]["run_attempt"] = 3
        data["prs"][0]["timeline"] = [{"event": "ready_for_review",
                                         "created_at": "2026-10-02T00:00:00Z"}]
        result = report.summarize(data)
        self.assertEqual(result["all_prs"]["distinct_head_rounds"], 1)
        self.assertEqual(result["same_head_overhead"]["additional_workflow_runs"], 1)
        self.assertEqual(result["same_head_overhead"]["draft_and_full_judged_heads"], 1)
        self.assertEqual(result["same_head_overhead"]["promotion_event_evidenced_heads"], 1)
        self.assertEqual(result["same_head_overhead"]["full_red_to_green_heads"], 0)
        self.assertEqual(result["same_head_overhead"]["rerun_attempts_observed"], 2)

    def test_full_red_to_green_is_reported_for_one_head(self):
        data = snapshot([run(1, "a", SINCE, [check(conclusion="FAILURE")]),
                         run(2, "a", "2026-10-03T00:00:00Z",
                             [check(at="2026-10-03T01:00:00Z")])])
        self.assertEqual(report.summarize(data)["same_head_overhead"]["full_red_to_green_heads"], 1)

    def test_full_success_supersedes_other_red_checks(self):
        data = snapshot([run(1, "a", SINCE, [check("backend / pure (0/1)", "FAILURE"), check()]),
                         run(2, "b", "2026-10-03T00:00:00Z")])
        result = report.summarize(data)
        self.assertEqual(result["extra_head_attribution"], {"unattributed": 1})
        self.assertEqual(result["prs"][0]["extra_heads"][0]["failures_before_push"], [])

    def test_counts_from_birth_not_merge_and_dedupes_return_to_old_head(self):
        data = snapshot([run(1, "a", SINCE), run(2, "b", "2026-10-02T00:00:00Z"),
                         run(3, "a", "2026-10-03T00:00:00Z")])
        data["prs"][0]["merged_at"] = "2026-10-10T00:00:00Z"
        self.assertEqual(report.summarize(data)["by_state_at_cutoff"]["merged"]["distinct_head_rounds"], 2)

    def test_draft_success_and_full_skipped_never_become_full_green(self):
        data = snapshot([run(1, "a", SINCE, [check(report.DRAFT), check(conclusion="SKIPPED")])])
        self.assertEqual(report.summarize(data)["full_judged_heads"], 0)

    def test_zero_round_pr_stays_in_denominator(self):
        result = report.summarize(snapshot())
        self.assertEqual(result["all_prs"], {"prs": 1, "distinct_head_rounds": 0, "mean": 0,
                                           "median": 0, "histogram": {0: 1}, "at_least_two": 0, "zero": 1})

    def test_observation_cutoff_excludes_later_run_and_judgement(self):
        data = snapshot([run(1, "a", SINCE, [check(at=UNTIL)]), run(2, "b", UNTIL)])
        data["prs"][0]["merged_at"] = UNTIL
        result = report.summarize(data)
        self.assertEqual(result["all_prs"]["distinct_head_rounds"], 1)
        self.assertEqual(result["full_judged_heads"], 0)
        self.assertEqual(result["by_state_at_cutoff"]["open"]["prs"], 1)

    def test_run_after_pr_close_is_not_a_lifetime_round(self):
        data = snapshot([run(1, "a", SINCE), run(2, "b", "2026-10-05T00:00:00Z")])
        data["prs"][0]["closed_at"] = "2026-10-03T00:00:00Z"
        result = report.summarize(data)
        self.assertEqual(result["all_prs"]["distinct_head_rounds"], 1)

    def test_timeline_scope_is_explicit_when_enrichment_is_bounded(self):
        data = snapshot()
        data["timelines_complete"] = False
        data["timeline_scope"] = "multi_head"
        result = report.summarize(data)
        self.assertEqual(result["cohort"]["timeline_scope"], "multi_head")
        self.assertFalse(result["timeline_evidence"]["complete"])
        self.assertIn("lower bounds", result["timeline_evidence"]["note"])

    def test_red_after_new_head_does_not_explain_push(self):
        data = snapshot([run(1, "a", SINCE, [check(conclusion="FAILURE", at="2026-10-03T00:00:00Z")]),
                         run(2, "b", "2026-10-02T00:00:00Z")])
        self.assertEqual(report.summarize(data)["extra_head_attribution"], {"unattributed": 1})

    def test_red_before_push_has_check_evidence_not_flake_claim(self):
        data = snapshot([run(1, "a", SINCE, [check(conclusion="FAILURE")]),
                         run(2, "b", "2026-10-03T00:00:00Z", [check()])])
        result = report.summarize(data)
        self.assertEqual(result["extra_head_attribution"], {"push_after_red": 1})
        self.assertEqual(result["prs"][0]["extra_heads"][0]["failures_before_push"][0]["run_id"], 1)
        self.assertIsNone(result["flaky_checks"]["verified"])

    def test_failure_resolved_on_same_head_does_not_explain_new_push(self):
        data = snapshot([run(1, "a", SINCE, [check(conclusion="FAILURE")]),
                         run(2, "a", "2026-10-02T02:00:00Z", [check(at="2026-10-02T03:00:00Z")]),
                         run(3, "b", "2026-10-03T00:00:00Z")])
        self.assertEqual(report.summarize(data)["extra_head_attribution"], {"unattributed": 1})

    def test_open_counts_are_explicitly_right_censored(self):
        result = report.summarize(snapshot())
        self.assertEqual(result["right_censored_prs"], 1)
        self.assertTrue(result["prs"][0]["right_censored"])
        self.assertEqual(result["prs"][0]["exposure_hours"], 336)

    def test_review_and_failure_overlap_is_explicit_not_double_counted(self):
        data = snapshot([run(1, "a", SINCE, [check(conclusion="FAILURE")]),
                         run(2, "b", "2026-10-03T00:00:00Z")])
        data["prs"][0]["timeline"] = [{"event": "reviewed", "state": "changes_requested",
                                        "submitted_at": "2026-10-02T02:00:00Z", "id": 12}]
        result = report.summarize(data)
        self.assertEqual(result["extra_head_attribution"], {"push_after_change_request": 1})
        self.assertEqual(result["extra_head_total"], 1)
        self.assertTrue(result["prs"][0]["extra_heads"][0]["failures_before_push"])

    def test_base_sync_subject_is_evidence_not_arbitrary_merge(self):
        data = snapshot([run(1, "a", SINCE), run(2, "b", "2026-10-03T00:00:00Z",
                         message="Merge remote-tracking branch 'origin/main' into task/x")])
        self.assertEqual(report.summarize(data)["extra_head_attribution"], {"base_sync_subject": 1})
        data["runs"][1]["commit_message"] = "Merge branch 'feature' into task/x"
        self.assertEqual(report.summarize(data)["extra_head_attribution"], {"unattributed": 1})

    def test_duplicate_runs_and_prs_fail_instead_of_inflating(self):
        data = snapshot([run(1, "a", SINCE)])
        for field in ("prs", "runs"):
            malformed = deepcopy(data)
            malformed[field].append(deepcopy(malformed[field][0]))
            with self.assertRaises(ValueError):
                report.summarize(malformed)


class Collector(unittest.TestCase):
    def test_normal_collection_forwards_multi_head_scope(self):
        with patch.object(collect, "collect") as mocked, patch.object(
            sys, "argv", ["ci_rounds_collect.py", "--cache", "/tmp/cache",
                           "--output", "/tmp/out", "--since", SINCE,
                           "--until", "2026-10-02T00:00:00Z", "--only-multi-head"]):
            collect.main()
        self.assertEqual(mocked.call_args.args[4:], (True, True))

    def test_timestamp_requires_utc(self):
        for text in ("2026-10-01", "2026-10-01T00:00:00+02:00"):
            with self.assertRaises(ValueError):
                collect.instant(text)

    def test_pr_birth_cohort_is_half_open_and_uses_pull_request_listing(self):
        raw = {"number": 1, "created_at": SINCE}
        def response(endpoint):
            self.assertIn("/pulls?", endpoint)
            return [raw, {"number": 2, "created_at": UNTIL}]
        with patch.object(collect, "api", side_effect=response):
            self.assertEqual(collect.prs("owner/repo", collect.instant(SINCE), collect.instant(UNTIL)), [raw])

    def test_pr_opening_between_pages_does_not_duplicate_cohort(self):
        original = [{"number": n, "created_at": SINCE} for n in range(200, 0, -1)]
        inserted = {"number": 201, "created_at": UNTIL}
        live = [inserted, *original]
        with patch.object(collect, "api", side_effect=[original[:100], live[100:200], live[200:]]):
            result = collect.prs("owner/repo", collect.instant(SINCE), collect.instant(UNTIL))
        self.assertEqual(len(result), 200)
        self.assertEqual({p["number"] for p in result}, set(range(1, 201)))

    def test_incomplete_run_page_is_error(self):
        with patch.object(collect, "api", return_value={"total_count": 2, "workflow_runs": [{}]}):
            with self.assertRaises(ValueError):
                collect.pages("endpoint", "workflow_runs")

    def test_1000_run_cap_splits_interval_and_dedupes_repeated_run(self):
        start = collect.instant(SINCE)
        end = start + timedelta(seconds=4)
        middle = collect.stamp(start + timedelta(seconds=2))
        def response(endpoint):
            if f"{SINCE}..{collect.stamp(end)}".replace(":", "%3A") in endpoint:
                return {"total_count": 1000, "workflow_runs": []}
            return {"total_count": 1, "workflow_runs": [{"id": 1, "created_at": middle}]}
        with patch.object(collect, "api", side_effect=response):
            self.assertEqual(collect.runs("owner/repo", start, end), [{"id": 1, "created_at": middle}])

    def test_github_pull_request_association_filters_cohort(self):
        first = {"number": 1}
        second = {"number": 2}
        row = {"id": 1, "pull_requests": [{"number": 1}]}
        self.assertEqual(collect.associate(row, [first, second]), [1])

    def test_deleted_branch_fallback_requires_unique_lifetime(self):
        pr = {"number": 1, "head": {"ref": "task/x", "repo": {"full_name": "owner/repo"}},
              "created_at": SINCE, "closed_at": None}
        row = {"id": 1, "head_branch": "task/x", "pull_requests": [],
               "repository": {"full_name": "owner/repo"}, "created_at": SINCE}
        self.assertEqual(collect.associate(row, [pr]), [1])
        other = deepcopy(pr)
        other["number"] = 2
        with self.assertRaises(ValueError):
            collect.associate(row, [pr, other])
        other["head"]["repo"]["full_name"] = "fork/repo"
        self.assertEqual(collect.associate(row, [other]), [])
        pr["closed_at"] = "2026-10-01T01:00:00Z"
        row["created_at"] = "2026-10-02T00:00:00Z"
        self.assertEqual(collect.associate(row, [pr]), [])

    def test_missing_or_truncated_suite_is_error(self):
        batch = [{"id": 1, "check_suite_node_id": "node"}]
        for value in (None, {"checkRuns": {"pageInfo": {"hasNextPage": True}, "nodes": []}}):
            with patch.object(collect.trace_retro, "_gh", return_value={"data": {"r0": value}}):
                with self.assertRaises(ValueError):
                    collect.suites(batch)


if __name__ == "__main__":
    unittest.main()
