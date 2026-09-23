import datetime as dt
import importlib.util
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ci_feedback_window", HERE / "ci-feedback-window.py")
window = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(window)


class FeedbackWindowTest(unittest.TestCase):
    def test_default_is_previous_settled_six_hour_block(self):
        self.assertEqual(
            window.cohort_window(dt.datetime(2026, 9, 24, 17, tzinfo=dt.UTC)),
            ("2026-09-24T00:00:00Z", "2026-09-24T05:59:59Z"),
        )

    def test_explicit_window_is_preserved_in_utc(self):
        self.assertEqual(
            window.cohort_window(
                dt.datetime(2026, 9, 24, tzinfo=dt.UTC),
                "2026-09-01T00:00:00Z",
                "2026-09-02T00:00:00Z",
            ),
            ("2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"),
        )

    def test_only_one_explicit_bound_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "supplied together"):
            window.cohort_window(
                dt.datetime(2026, 9, 24, tzinfo=dt.UTC),
                "2026-09-01T00:00:00Z",
                "",
            )

    def test_cli_records_exact_inputs_and_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            code = window.main([
                "--now", "2026-09-24T17:00:00Z",
                "--github-output", str(root / "output"),
                "--parameters", str(root / "parameters.json"),
            ])
            self.assertEqual(code, 0)
            self.assertEqual(
                (root / "output").read_text(),
                "since=2026-09-24T00:00:00Z\nuntil=2026-09-24T05:59:59Z\n",
            )
            self.assertIn(
                '"since": "2026-09-24T00:00:00Z"',
                (root / "parameters.json").read_text(),
            )


class FeedbackWorkflowTest(unittest.TestCase):
    def test_recurring_workflow_is_read_only_and_retains_failed_evidence(self):
        text = (HERE.parent / "workflows" / "ci-feedback.yml").read_text()
        self.assertIn("schedule:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("actions: read", text)
        self.assertIn("contents: read", text)
        self.assertIn("ci-feedback-report.py", text)
        self.assertIn("if: always()", text)
        self.assertIn("retention-days: 90", text)
        self.assertRegex(text, r"actions/checkout@[0-9a-f]{40}")
        self.assertRegex(text, r"actions/upload-artifact@[0-9a-f]{40}")


if __name__ == "__main__":
    unittest.main()
