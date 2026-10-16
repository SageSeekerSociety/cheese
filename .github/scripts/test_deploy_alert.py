"""Eight dev deploys failed on 2026-10-14 and the alert group heard of none."""

import importlib.util
import pathlib
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("deploy_alert", _HERE / "deploy-alert.py")
alert = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(alert)


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
        reason = alert.assess(job("failure", DEPLOY_FAILED), 180, set())
        self.assertIsNotNone(reason)
        self.assertIn("Docker deploy this commit", reason)

    def test_a_runner_lost_mid_deploy_pages(self) -> None:
        """The runner went away, so no step failed and nothing on the box could say so."""
        self.assertIsNotNone(alert.assess(job("failure", []), 180, set()))

    def test_a_run_a_newer_deploy_replaced_stays_quiet(self) -> None:
        """A pending run cancelled in the concurrency group: no runner, no steps."""
        self.assertIsNone(alert.assess(job("cancelled", [], runner="", minutes=2), 180, set()))

    def test_a_person_cancelling_a_running_deploy_stays_quiet(self) -> None:
        steps = [("Docker deploy this commit", "cancelled")]
        self.assertIsNone(alert.assess(job("cancelled", steps, minutes=20), 180, set()))

    def test_a_deploy_that_hangs_for_its_whole_timeout_pages(self) -> None:
        """Expressions call a timed-out job cancelled; it is still a deploy that did not happen."""
        steps = [("Docker deploy this commit", "cancelled")]
        reason = alert.assess(job("cancelled", steps, minutes=180), 180, set())
        self.assertIsNotNone(reason)
        self.assertIn("Docker deploy this commit", reason)
        self.assertIsNotNone(alert.assess(job("timed_out", steps, minutes=180), 180, set()))

    def test_a_release_gate_refusing_stays_quiet(self) -> None:
        """An approver saying deny, or nobody approving in time, is a decision."""
        gate = {"Require manual approval"}
        denied = job("failure", [("Require manual approval", "failure")])
        unanswered = job("cancelled", [("Require manual approval", "cancelled")], minutes=360)
        self.assertIsNone(alert.assess(denied, 360, gate))
        self.assertIsNone(alert.assess(unanswered, 360, gate))

    def test_success_and_skip_stay_quiet(self) -> None:
        self.assertIsNone(alert.assess(job("success"), 180, set()))
        self.assertIsNone(alert.assess(job("skipped", runner=""), 180, set()))
        self.assertIsNone(alert.assess(None, 180, set()))

    def test_the_message_is_marked_severe_and_says_where_and_what(self) -> None:
        text = alert.message("dev（okcheese.com）", "d1d4e65d7da7d4d44e5978a64040f5f846db1376",
                             "失败的步骤：Docker deploy this commit", "https://example/run/1")
        self.assertTrue(text.startswith("【严重】"))
        for part in ("dev（okcheese.com）", "d1d4e65d7da7", "Docker deploy this commit",
                     "https://example/run/1"):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
