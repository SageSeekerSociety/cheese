"""A scan that did not run must never be summarized as a clean result."""

import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = _HERE / "audit_summary.py"
_spec = importlib.util.spec_from_file_location("audit_summary", SCRIPT)
audit_summary = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = audit_summary  # dataclasses looks the module up by name
_spec.loader.exec_module(audit_summary)


def descriptor(title, outcome, *, covers="shipped dependencies"):
    return f"{title}|{title}|{covers}|{outcome}"


class AuditSummary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.summary = pathlib.Path(self._tmp.name) / "summary.md"

    def argv(self, *descriptors):
        argv = []
        for raw in descriptors:
            argv += ["--scan", raw]
        return argv + ["--summary", str(self.summary)]

    def run_summary(self, *descriptors):
        return audit_summary.main(self.argv(*descriptors)), self.summary.read_text()

    def test_a_run_where_every_scan_succeeded_says_the_dependencies_are_clean(self):
        code, text = self.run_summary(
            descriptor("pnpm", "success"), descriptor("pip", "success")
        )

        self.assertEqual(code, 0)
        self.assertIn("All scans ran and found nothing", text)
        self.assertNotIn("did not run", text)

    def test_a_skipped_scan_is_never_reported_as_clean(self):
        code, text = self.run_summary(
            descriptor("pnpm", "success"), descriptor("pip", "skipped")
        )

        self.assertEqual(code, 1)
        self.assertIn("**scan did not run**", text)
        row = next(line for line in text.splitlines() if line.startswith("| `pip`"))
        self.assertIn("did not run", row)

    def test_a_cancelled_scan_is_not_a_pass(self):
        code, text = self.run_summary(descriptor("pnpm", "cancelled"))

        self.assertEqual(code, 1)
        self.assertIn("scan did not run", text)

    def test_an_outcome_that_never_got_set_is_not_a_pass(self):
        code, text = self.run_summary(descriptor("pnpm", ""))

        self.assertEqual(code, 1)
        self.assertIn("scan did not run", text)

    def test_findings_fail_the_run(self):
        code, text = self.run_summary(descriptor("pnpm", "failure"))

        self.assertEqual(code, 1)
        self.assertIn("findings", text)

    def test_only_the_scans_that_were_not_clean_are_listed_under_not_clean(self):
        _, text = self.run_summary(
            descriptor("pnpm", "success"), descriptor("pip", "skipped")
        )

        section = text.split("### Not clean", 1)[1]
        self.assertIn("`pip`", section)
        self.assertNotIn("`pnpm`", section)

    def test_the_summary_is_written_where_the_run_page_reads_it(self):
        with mock.patch.dict(
            os.environ, {"GITHUB_STEP_SUMMARY": str(self.summary)}, clear=False
        ):
            code = audit_summary.main(["--scan", descriptor("pnpm", "success")])

        self.assertEqual(code, 0)
        self.assertIn("## Dependency audit", self.summary.read_text())

    def test_each_unclean_scan_gets_a_warning_annotation(self):
        argv = self.argv(descriptor("pnpm", "skipped"), descriptor("pip", "success"))

        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            audit_summary.main(argv)

        self.assertIn("::warning title=pnpm::", out.getvalue())
        self.assertNotIn("::warning title=pip::", out.getvalue())

    def test_the_process_exit_code_is_the_verdict(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--scan",
                descriptor("pnpm", "skipped"),
                "--summary",
                str(self.summary),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertEqual(result.returncode, 1, result.stderr)


if __name__ == "__main__":
    unittest.main()
