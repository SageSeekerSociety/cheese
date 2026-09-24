import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("ci-test-evidence.py")
SPEC = importlib.util.spec_from_file_location("ci_test_evidence", SCRIPT)
assert SPEC and SPEC.loader
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


class PlaywrightEvidenceTests(unittest.TestCase):
    def test_cli_writes_only_the_normalized_clean_schema(self):
        report = {
            "suites": [
                {
                    "specs": [
                        {
                            "tests": [
                                {
                                    "title": "login",
                                    "status": "expected",
                                    "results": [{"retry": 0, "status": "passed"}],
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "report.json"
            output = root / "artifact" / "evidence.json"
            source.write_text(json.dumps(report))
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--suite",
                    "e2e",
                    "--source",
                    str(source),
                    "--output",
                    str(output),
                    "--run-id",
                    "17",
                    "--run-attempt",
                    "2",
                    "--head-sha",
                    "abc123",
                    "--tested-sha",
                    "merge456",
                    "--steps-json",
                    json.dumps({"e2e": {"outcome": "success"}}),
                    "--require-step",
                    "e2e",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stdout, output.read_text())
            self.assertEqual(
                json.loads(output.read_text()),
                {
                    "schema_version": 1,
                    "run_id": 17,
                    "run_attempt": 2,
                    "head_sha": "abc123",
                    "tested_sha": "merge456",
                    "suite": "e2e",
                    "clean": True,
                    "reason": "all required tests and steps passed without retries or skips",
                    "tests": 1,
                    "retries": 0,
                    "skipped": 0,
                },
            )

    def test_first_failure_then_retry_is_not_clean(self):
        report = {
            "suites": [
                {
                    "specs": [
                        {
                            "tests": [
                                {
                                    "title": "login",
                                    "status": "flaky",
                                    "results": [
                                        {"retry": 0, "status": "failed"},
                                        {"retry": 1, "status": "passed"},
                                    ],
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            path.write_text(json.dumps(report))
            self.assertEqual(evidence._playwright(path), (1, 1, 0, ["login"]))

    def test_clean_report_has_one_passed_attempt_per_test(self):
        report = {
            "suites": [
                {
                    "specs": [
                        {
                            "tests": [
                                {
                                    "title": "login",
                                    "status": "expected",
                                    "results": [{"retry": 0, "status": "passed"}],
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            path.write_text(json.dumps(report))
            self.assertEqual(evidence._playwright(path), (1, 0, 0, []))

    def test_empty_and_skipped_reports_cannot_be_clean(self):
        empty = {"suites": []}
        skipped = {
            "suites": [
                {
                    "specs": [
                        {
                            "tests": [
                                {
                                    "title": "optional",
                                    "status": "skipped",
                                    "results": [{"retry": 0, "status": "skipped"}],
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            path.write_text(json.dumps(empty))
            self.assertEqual(evidence._playwright(path), (0, 0, 0, []))
            path.write_text(json.dumps(skipped))
            self.assertEqual(evidence._playwright(path), (1, 0, 1, ["optional"]))


class RemoteEvidenceTests(unittest.TestCase):
    def write_results(self, folder: Path) -> Path:
        path = folder / "results.json"
        path.write_text(
            json.dumps({name: {"passed": True} for name in evidence.REMOTE_CASES})
        )
        for name in evidence.REMOTE_CASES:
            (folder / f"{name}.attempt-1.log").write_text("passed")
        (folder / "invocation.json").write_text('{"count": 1}')
        (folder / "backend-regressions.xml").write_text(
            '<testsuite><testcase name="backend"/></testsuite>'
        )
        return path

    def test_clean_remote_suite_has_every_named_case_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_results(Path(folder))
            self.assertEqual(evidence._remote(path), (8, 0, 0, []))

    def test_resumed_remote_case_is_not_clean(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.write_results(root)
            (root / "executor.attempt-2.log").write_text("passed after resume")
            self.assertEqual(evidence._remote(path), (8, 1, 0, []))

    def test_resumed_suite_is_not_clean_even_when_each_case_ran_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.write_results(root)
            (root / "invocation.json").write_text('{"count": 2}')
            self.assertEqual(
                evidence._remote(path),
                (8, 1, 0, ["remote suite ran 2 times in one output"]),
            )

    def test_skipped_backend_regression_is_not_clean(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.write_results(root)
            (root / "backend-regressions.xml").write_text(
                '<testsuite><testcase name="backend"><skipped/></testcase></testsuite>'
            )
            self.assertEqual(evidence._remote(path), (8, 0, 1, []))

    def test_missing_remote_case_is_malformed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = self.write_results(root)
            data = json.loads(path.read_text())
            data.pop("executor")
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "missing=.*executor"):
                evidence._remote(path)


class JunitAndStepEvidenceTests(unittest.TestCase):
    def test_junit_counts_failures_and_skips(self):
        xml = """<testsuite><testcase name="pass"/><testcase name="skip"><skipped/></testcase><testcase name="fail"><failure/></testcase></testsuite>"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.xml"
            path.write_text(xml)
            self.assertEqual(evidence._junit(path, 1), (3, 0, 1, ["fail"]))

    def test_junit_floor_rejects_collapsed_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.xml"
            path.write_text('<testsuite><testcase name="only"/></testsuite>')
            self.assertEqual(
                evidence._junit(path, 220),
                (1, 0, 0, ["only 1 tests ran; expected at least 220"]),
            )

    def test_missing_or_failed_required_step_is_not_clean(self):
        raw = json.dumps({"tests": {"outcome": "success"}})
        self.assertEqual(
            evidence._steps(raw, ["tests", "terminal"]),
            (False, "required steps did not pass: terminal"),
        )
        self.assertEqual(
            evidence._steps("not json", ["tests"]),
            (False, "workflow step outcomes are missing or malformed"),
        )


if __name__ == "__main__":
    unittest.main()
