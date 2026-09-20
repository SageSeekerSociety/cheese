"""Behavioral checks for scheduler gaps and actual heartbeat execution failures."""

import importlib.util
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "box_uptime", Path(__file__).with_name("box-uptime.py")
)
monitor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = monitor
spec.loader.exec_module(monitor)

NOW = datetime(2026, 9, 9, 20, tzinfo=UTC)


def run(age_minutes, conclusion="success", status="completed", run_id=1):
    return {
        "id": run_id,
        "created_at": (NOW - timedelta(minutes=age_minutes)).isoformat(),
        "status": status,
        "conclusion": conclusion,
    }


class HeartbeatTests(unittest.TestCase):
    def test_fresh_success(self):
        result = monitor.assess([run(10), run(80, run_id=2)], NOW)
        self.assertEqual((result.errors, result.warnings), ([], []))

    def test_delayed_scheduler_does_not_report_box_failure(self):
        for gap in (156, 232, 392, 480, 756):
            with self.subTest(gap=gap):
                result = monitor.assess([run(gap), run(gap + 60, run_id=2)], NOW)
                self.assertFalse(result.errors)
                self.assertTrue(result.warnings)
                self.assertIn("unknown", result.warnings[0])

    def test_stale_history_does_not_hide_consecutive_failures(self):
        result = monitor.assess(
            [run(232, "failure"), run(300, "cancelled", run_id=2)], NOW
        )
        self.assertTrue(result.errors)
        self.assertEqual(result.inspect_runs, [1, 2])

    def test_unfinished_run_backstop_is_reachable(self):
        result = monitor.assess(
            [run(181, None, "queued"), run(260, run_id=2), run(330, run_id=3)], NOW
        )
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.inspect_runs, [1])

    def test_single_failure_does_not_page(self):
        result = monitor.assess([run(10, "failure"), run(80, run_id=2)], NOW)
        self.assertFalse(result.errors)

    def test_pending_new_run_does_not_hide_two_failed_completed_runs(self):
        result = monitor.assess(
            [
                run(5, None, "in_progress"),
                run(70, "cancelled", run_id=2),
                run(140, "failure", run_id=3),
            ],
            NOW,
        )
        self.assertTrue(result.errors)
        self.assertEqual(result.inspect_runs, [2, 3])

    def test_missing_and_insufficient_history_are_explicit(self):
        self.assertIn("unknown", monitor.assess([], NOW).summary)
        self.assertTrue(monitor.assess([], NOW).errors)
        self.assertTrue(monitor.assess([run(10)], NOW).warnings)

    def test_unsorted_response_uses_most_recent_runs(self):
        result = monitor.assess(
            [run(200, "failure"), run(10, run_id=2), run(80, run_id=3)], NOW
        )
        self.assertFalse(result.errors)
        self.assertFalse(result.warnings)


if __name__ == "__main__":
    unittest.main()
