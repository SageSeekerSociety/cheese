import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "rerun_failed", Path(__file__).with_name("rerun-failed.py")
)
rerun = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rerun)

REPO = "SageSeekerSociety/cheese"


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


if __name__ == "__main__":
    unittest.main()
