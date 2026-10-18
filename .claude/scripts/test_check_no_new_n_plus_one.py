#!/usr/bin/env python3
"""Self-test check-no-new-n-plus-one.py.

The guard's whole claim is that the detector can still tell an N+1 from a page
that reads one row twice: the same call site *and* the same SQL shape, not the
same statement count alone. This file runs the guard against the real detector
and then mutates the detector in the two ways the report named — count without
the call site, and fingerprint by returning the statement unchanged — and
asserts the guard goes red each time. Those mutations are what prove the cases
are held by the judgement and not by a guard that would agree with anything.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check-no-new-n-plus-one.py")
MODULE = SCRIPT.parents[2] / "backend" / "app" / "core" / "query_watch.py"

#: The judgement's two halves, as they read in the detector's source.
STACK_ANCHOR = "    return (stack, fingerprint)"
FINGERPRINT_ANCHOR = (
    "    shape = _DOLLAR_QUOTED.sub(\"?\", statement)\n"
    "    shape = _SINGLE_QUOTED.sub(\"?\", shape)"
)


class NPlusOneGuardTests(unittest.TestCase):
    def test_the_real_detector_is_judged_as_claimed(self) -> None:
        result = self._run(SCRIPT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("通过", result.stdout)

    def test_a_missing_detector_cannot_be_judged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "query_watch.py"
            result = self._run(SCRIPT, "--module", str(missing))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("判不了", result.stderr)

    def test_a_stack_blind_judgement_turns_this_guard_red(self) -> None:
        # The report's first deliberate violation: count by shape alone. Then
        # "same fingerprint, different call sites" — two pages asking the same
        # question — is reported as an N+1, and that case must fail.
        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                lambda source: source.replace(
                    STACK_ANCHOR, "    return ((), fingerprint)", 1
                ),
                STACK_ANCHOR,
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("同指纹不同栈", result.stderr)

    def test_a_fingerprint_that_keeps_literals_turns_this_guard_red(self) -> None:
        # The report's second deliberate violation: the fingerprint returns the
        # statement unchanged, so three statements differing only in a bound id
        # stop being one shape and the loop goes unreported.
        override = (
            "\n\ndef fingerprint_sql(statement):\n"
            "    return statement\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                lambda source: source + override,
                "def fingerprint_sql",
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("同指纹同栈", result.stderr)

    def test_a_detector_that_never_reports_turns_this_guard_red(self) -> None:
        override = (
            "\n\ndef group_key(stack, fingerprint):\n"
            "    return (stack, fingerprint, object())\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                lambda source: source + override,
                "def group_key",
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("应报，实际无", result.stderr)

    def test_a_fingerprint_that_erases_every_difference_turns_this_guard_red(
        self,
    ) -> None:
        # The opposite loosening: every statement becomes one shape, so two
        # unrelated queries at one call site — and two different tables in
        # double-quoted identifiers — are reported as an N+1.
        override = (
            "\n\ndef fingerprint_sql(statement):\n"
            "    return \"?\"\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                lambda source: source + override,
                "def fingerprint_sql",
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("不同指纹", result.stderr)

    def test_json_names_the_case_that_failed(self) -> None:
        override = (
            "\n\ndef fingerprint_sql(statement):\n"
            "    return statement\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                lambda source: source + override,
                "def fingerprint_sql",
            )
            result = self._run(SCRIPT, "--module", str(module), "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(result.stdout)
        names = [failure["case"] for failure in payload["failures"]]
        self.assertIn("同指纹同栈 3 次 → 报", names)
        self.assertEqual(len(payload["failures"]), len(names))

    def test_the_real_detector_has_the_fingerprint_this_guard_pins(self) -> None:
        # If the fingerprint is rewritten rather than loosened, the mutations
        # below would stop landing. Say so here instead of passing silently.
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn(FINGERPRINT_ANCHOR, source)
        self.assertIn(STACK_ANCHOR, source)

    @staticmethod
    def _mutate(directory: str, change, anchor: str) -> Path:
        source = MODULE.read_text(encoding="utf-8")
        assert anchor in source, f"变异锚点不在探测器源码里: {anchor!r}"
        mutated = change(source)
        assert mutated != source, "变异没有落到探测器源码上"
        target = Path(directory) / "query_watch.py"
        target.write_text(mutated, encoding="utf-8")
        return target

    @staticmethod
    def _run(script: Path, *arguments: str):
        return subprocess.run(
            [sys.executable, str(script), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
