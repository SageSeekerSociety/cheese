"""Eight dev deploys failed on 2026-10-14 and the alert group heard of none."""

import importlib.util
import io
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("deploy_alert", _HERE / "deploy-alert.py")
alert = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(alert)

GUARD = "Keep automatic deployments from going backwards"
APPROVAL = "Require manual approval"


def job(conclusion, steps=(), runner="cheese-dev-env1", minutes=10):
    return {
        "name": "deploy",
        "conclusion": conclusion,
        "runner_name": runner,
        "started_at": "2026-10-14T10:00:00Z",
        "completed_at": f"2026-10-14T{10 + minutes // 60:02d}:{minutes % 60:02d}:00Z",
        "steps": [{"name": name, "status": "completed", "conclusion": c} for name, c in steps],
    }


DEPLOY_FAILED = [("Log in to ghcr", "success"), ("Docker deploy this commit", "failure"),
                 ("Release the verified metering image", "skipped")]


class DeployAlert(unittest.TestCase):
    def test_a_failed_deploy_step_pages_and_names_the_step(self) -> None:
        reason = alert.assess(job("failure", DEPLOY_FAILED), 180)
        self.assertIsNotNone(reason)
        self.assertIn("Docker deploy this commit", reason)

    def test_a_runner_lost_mid_deploy_pages(self) -> None:
        """The runner went away, so no step failed and nothing on the box could say so."""
        self.assertIsNotNone(alert.assess(job("failure", []), 180))

    def test_a_run_a_newer_deploy_replaced_stays_quiet(self) -> None:
        """A pending run cancelled in the concurrency group: no runner, no steps. Its
        start time is when it began waiting, so a run that waited behind a deploy
        that hung for the whole timeout looks as long as a timeout itself."""
        self.assertIsNone(alert.assess(job("cancelled", [], runner="", minutes=2), 180))
        self.assertIsNone(alert.assess(job("cancelled", [], runner="", minutes=185), 180))

    def test_a_person_cancelling_a_running_deploy_stays_quiet(self) -> None:
        steps = [("Docker deploy this commit", "cancelled")]
        self.assertIsNone(alert.assess(job("cancelled", steps, minutes=20), 180))

    def test_a_deploy_that_hangs_for_its_whole_timeout_pages(self) -> None:
        """Expressions call a timed-out job cancelled; it is still a deploy that did not happen."""
        steps = [("Docker deploy this commit", "cancelled")]
        reason = alert.assess(job("cancelled", steps, minutes=180), 180)
        self.assertIsNotNone(reason)
        self.assertIn("Docker deploy this commit", reason)
        self.assertIsNotNone(alert.assess(job("timed_out", steps, minutes=180), 180))

    def test_a_release_gate_refusing_stays_quiet(self) -> None:
        """The gate said its validation no longer holds, or an approver said deny."""
        self.assertIsNone(alert.assess(job("failure", [(GUARD, "failure")]), 180, refused=True))
        self.assertIsNone(alert.assess(job("failure", [(APPROVAL, "failure")]), 360, refused=True))

    def test_the_gate_step_crashing_still_pages(self) -> None:
        """Deploy run 37710891132 failed in the guard on a GitHub read that kept
        dropping: the same step as a refusal, and the deploy did not happen."""
        reason = alert.assess(job("failure", [(GUARD, "failure")]), 180, refused=False)
        self.assertIsNotNone(reason)
        self.assertIn(GUARD, reason)

    def test_nobody_approving_in_time_stays_quiet(self) -> None:
        waiting = job("cancelled", [(APPROVAL, "cancelled")], runner="GitHub Actions 3", minutes=360)
        self.assertIsNone(alert.assess(waiting, 360, waits_for_a_person=APPROVAL))
        after = job("cancelled", [(APPROVAL, "success"), ("Deploy via SSH", "cancelled")],
                    runner="GitHub Actions 3", minutes=360)
        self.assertIsNotNone(alert.assess(after, 360, waits_for_a_person=APPROVAL))

    def test_success_and_skip_stay_quiet(self) -> None:
        self.assertIsNone(alert.assess(job("success"), 180))
        self.assertIsNone(alert.assess(job("skipped", runner=""), 180))
        self.assertIsNone(alert.assess(None, 180))

    def test_the_message_is_marked_severe_and_says_where_and_what(self) -> None:
        text = alert.message("dev（okcheese.com）", "d1d4e65d7da7d4d44e5978a64040f5f846db1376",
                             "失败的步骤：Docker deploy this commit", "https://example/run/1")
        self.assertTrue(text.startswith("【严重】"))
        for part in ("dev（okcheese.com）", "d1d4e65d7da7", "Docker deploy this commit",
                     "https://example/run/1"):
            self.assertIn(part, text)


class TheStepTheWorkflowRuns(unittest.TestCase):
    """What the workflow's post step receives, from the environment it sets."""

    def run_main(self, jobs=None, error=None, **extra):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "output"
            env = {"GH_TOKEN": "t", "REPO": "o/r", "RUN_ID": "1", "RUN_ATTEMPT": "1",
                   "RUN_URL": "https://example/run/1", "ENVIRONMENT": "dev（okcheese.com）",
                   "COMMIT": "d1d4e65d7da7d4d44e5978a64040f5f846db1376", "TIMEOUT_MINUTES": "180",
                   "GITHUB_OUTPUT": str(output), **extra}
            fetch = patch.object(alert, "fetch_jobs", side_effect=error) if error else \
                patch.object(alert, "fetch_jobs", return_value=jobs)
            with patch.dict(os.environ, env, clear=True), fetch:
                alert.main()
            return output.read_text() if output.exists() else ""

    def test_a_failed_deploy_reaches_the_post_step(self) -> None:
        others = {"name": "eligibility", "conclusion": "success", "steps": []}
        written = self.run_main([others, job("failure", DEPLOY_FAILED)])
        self.assertIn("【严重】部署失败：dev（okcheese.com）", written)
        self.assertIn("Docker deploy this commit", written)

    def test_a_refusal_the_workflow_passes_on_reaches_nobody(self) -> None:
        self.assertEqual(self.run_main([job("failure", [(GUARD, "failure")])], REFUSED="true"), "")

    def test_one_dropped_read_of_the_job_list_is_read_again(self) -> None:
        body = io.BytesIO(json.dumps({"jobs": [job("failure", DEPLOY_FAILED)]}).encode())
        answers = [urllib.error.URLError("reset"), body]
        with patch.object(alert.urllib.request, "urlopen", side_effect=answers), \
                patch.object(alert.time, "sleep"):
            jobs = alert.fetch_jobs("o/r", "1", "1", "t")
        self.assertEqual(jobs[0]["conclusion"], "failure")

    def test_an_unreadable_job_list_still_pages_a_failed_deploy(self) -> None:
        down = urllib.error.URLError("unreachable")
        self.assertIn("【严重】", self.run_main(error=down, DEPLOY_RESULT="failure"))
        self.assertEqual(self.run_main(error=down, DEPLOY_RESULT="cancelled"), "")


if __name__ == "__main__":
    unittest.main()
