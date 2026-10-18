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
        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                lambda source: source.replace(
                    FINGERPRINT_ANCHOR, "    return statement", 1
                ),
                FINGERPRINT_ANCHOR,
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("同指纹同栈", result.stderr)

    def test_a_fingerprint_that_stops_folding_dollar_quotes_turns_this_guard_red(
        self,
    ) -> None:
        # The documented normalization is pinned by cases, not by reading the
        # regex: erasing either rule has to be noticed.
        def erase(source: str) -> str:
            broken = source.replace(
                '_DOLLAR_QUOTED = re.compile(r"\\$[A-Za-z_0-9]*\\$.*?\\$[A-Za-z_0-9]*\\$", re.DOTALL)',
                '_DOLLAR_QUOTED = re.compile(r"a^")',
                1,
            )
            return broken.replace(
                '_PLACEHOLDER = re.compile(r"\\$\\d+|%\\([^)]+\\)s")',
                '_PLACEHOLDER = re.compile(r"\\$\\d+")',
                1,
            )

        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(directory, erase, "_DOLLAR_QUOTED = re.compile")
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("dollar-quoted", result.stderr)
        self.assertIn("命名占位符", result.stderr)

    def test_a_note_that_raises_instead_of_swallowing_turns_this_guard_red(
        self,
    ) -> None:
        # "Only logs, never raises" is the module's promise. Rewriting the
        # swallow into a re-raise has to be caught by the guard. Note and
        # report are mutated one at a time: sharing a try in the check made a
        # raising note() end the case before the report was ever exercised.
        def re_raise(source: str) -> str:
            return source.replace(
                'logger.debug("query watch could not record a statement", exc_info=True)',
                "raise",
                1,
            )

        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                re_raise,
                'logger.debug("query watch could not record a statement"',
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("记录与报告不抛异常", result.stderr)

    def test_a_report_that_raises_instead_of_swallowing_turns_this_guard_red(
        self,
    ) -> None:
        def re_raise(source: str) -> str:
            return source.replace(
                '        logger.debug("query watch could not report a request", exc_info=True)\n'
                "        return []",
                "        raise",
                1,
            )

        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                re_raise,
                'logger.debug("query watch could not report a request"',
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("记录与报告不抛异常", result.stderr)

    def test_a_finish_that_leaves_the_watch_open_turns_this_guard_red(self) -> None:
        # finish() closes the watch so a task that inherited it stops counting.
        def leave_open(source: str) -> str:
            return source.replace(
                "    watch.closed = True\n    return report(watch)",
                "    return report(watch)",
                1,
            )

        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                leave_open,
                "    watch.closed = True\n    return report(watch)",
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("begin/finish 边界", result.stderr)

    def test_a_dollar_quote_that_keeps_its_tag_turns_this_guard_red(self) -> None:
        # $$...$$ and $tag$...$tag$ are both documented; a case has to cover
        # each, or dropping the tagged branch goes unnoticed.
        def only_untagged(source: str) -> str:
            return source.replace(
                '_DOLLAR_QUOTED = re.compile(r"\\$[A-Za-z_0-9]*\\$.*?\\$[A-Za-z_0-9]*\\$", re.DOTALL)',
                '_DOLLAR_QUOTED = re.compile(r"\\$\\$.*?\\$\\$", re.DOTALL)',
                1,
            )

        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                only_untagged,
                "_DOLLAR_QUOTED = re.compile",
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("$tag$", result.stderr)

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

    def test_a_report_that_stops_logging_turns_this_guard_red(self) -> None:
        # The log line is the detector's only output: a report that returns
        # its repeats without logging would pass every other case, because
        # they run with the logger silenced and assert the return value.
        def stop_logging(source: str) -> str:
            return source.replace(
                "        for item in repeats:\n"
                "            logger.warning(",
                "        for item in []:\n"
                "            logger.warning(",
                1,
            )

        with tempfile.TemporaryDirectory() as directory:
            module = self._mutate(
                directory,
                stop_logging,
                "            logger.warning(",
            )
            result = self._run(SCRIPT, "--module", str(module))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("没有为重复组打出日志", result.stderr)

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
