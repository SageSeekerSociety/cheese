import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
