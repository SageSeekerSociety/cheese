#!/usr/bin/env python3
"""Behavioral tests for verify-brief-paths.py using throwaway git repositories."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("verify-brief-paths.py")
spec = importlib.util.spec_from_file_location("verify_brief_paths", SCRIPT)
verify = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verify)


class VerifyBriefPathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / "src").mkdir()
        (self.root / "src/existing.py").write_text("present\n", encoding="utf-8")
        (self.root / "src/overlap.py").write_text("present\n", encoding="utf-8")
        (self.root / "Makefile").write_text("all:\n\ttrue\n", encoding="utf-8")
        self.git("add", "src", "Makefile")
        self.git("commit", "-qm", "initial")
        self.initial = self.git("rev-parse", "HEAD").strip()
        self.git("update-ref", "refs/remotes/origin/main", self.initial)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout

    def run_tool(self, brief: str, *options: str):
        brief_file = self.root / "brief.txt"
        brief_file.write_text(brief, encoding="utf-8")
        return self.run_args(["--brief", str(brief_file), *options])

    def run_args(self, args: list[str], stdin_text: str = ""):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(stdin_text)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = verify.main(["--root", str(self.root), *args])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_bad_brief_reports_nonexistent_path(self):
        result, _, errors = self.run_tool("Inspect src/missing.py\n")
        self.assertEqual(result, 1)
        self.assertIn("path does not exist", errors)
        self.assertIn("src/missing.py", errors)

    def test_actual_and_declared_in_flight_sets_are_each_checked(self):
        for option in ("--in-flight-actual", "--in-flight-declared"):
            with self.subTest(option=option):
                result, _, errors = self.run_tool(
                    "Use src/overlap.py\n", option, "src/overlap.py"
                )
                self.assertEqual(result, 1)
                self.assertIn("overlaps in-flight", errors)
                self.assertIn("src/overlap.py", errors)

    def test_chinese_adjacent_to_path_does_not_corrupt_it(self):
        result, output, errors = self.run_tool("修改src/existing.py中的函数。\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("verified 1 brief path", output)

    def test_chinese_adjacent_overlap_reports_the_path(self):
        result, _, errors = self.run_tool(
            "修改src/overlap.py，注意与在飞 PR 重叠。\n",
            "--in-flight-actual", "src/overlap.py",
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("src/overlap.py", errors)

    def test_explicit_paths_do_not_read_implicit_stdin(self):
        class UnexpectedInput:
            def read(self):
                raise AssertionError("stdin should not be read in explicit-path mode")

        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("sys.stdin", UnexpectedInput()),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = verify.main(["--root", str(self.root), "--brief-path", "src/existing.py"])
        self.assertEqual(result, 0, stderr.getvalue())
        self.assertIn("verified 1 brief path", stdout.getvalue())

    def test_paths_present_in_either_ref_are_accepted(self):
        (self.root / "src/origin-only.py").write_text("origin\n", encoding="utf-8")
        self.git("add", "src/origin-only.py")
        self.git("commit", "-qm", "origin-only")
        origin_commit = self.git("rev-parse", "HEAD").strip()
        self.git("update-ref", "refs/remotes/origin/main", origin_commit)
        self.git("reset", "--hard", self.initial)
        result, _, errors = self.run_tool("Use src/origin-only.py\n")
        self.assertEqual(result, 0, errors)

        (self.root / "src/head-only.py").write_text("head\n", encoding="utf-8")
        self.git("add", "src/head-only.py")
        self.git("commit", "-qm", "head-only")
        self.git("update-ref", "refs/remotes/origin/main", self.initial)
        result, _, errors = self.run_tool("Use src/head-only.py\n")
        self.assertEqual(result, 0, errors)

    def test_compliant_brief_accepts_existing_and_new_paths(self):
        result, output, errors = self.run_tool(
            "Use src/existing.py; 新建 .claude/new-helper.py\n",
            "--in-flight-actual", "src/unrelated.py",
            "--in-flight-declared", "docs/unrelated.md",
        )
        self.assertEqual(result, 0, errors)
        self.assertIn("OK:", output)
        self.assertEqual(errors, "")

    def test_new_in_prose_does_not_mark_an_unrelated_word_as_a_path(self):
        result, _, errors = self.run_tool("A new feature uses src/existing.py.\n")
        self.assertEqual(result, 0, errors)

    def test_root_extensionless_paths_are_extracted_and_checked(self):
        result, _, errors = self.run_tool("Update Makefile\n")
        self.assertEqual(result, 0, errors)
        result, _, errors = self.run_tool(
            "Update Makefile\n", "--in-flight-actual", "Makefile"
        )
        self.assertEqual(result, 1)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("Makefile", errors)

    def test_new_marker_extracts_untracked_extensionless_path(self):
        result, _, errors = self.run_tool("新建 Dockerfile\n")
        self.assertEqual(result, 0, errors)

    def test_new_marker_for_existing_path_is_red(self):
        result, _, errors = self.run_tool("新建 src/existing.py\n")
        self.assertEqual(result, 1)
        self.assertIn("marked new but already exists", errors)

    def test_stdin_cannot_be_consumed_as_both_brief_and_list(self):
        for option in ("--in-flight-actual-list", "--in-flight-declared-list"):
            with self.subTest(option=option):
                result, _, errors = self.run_args(
                    [option, "-"], "Use src/overlap.py\nsrc/overlap.py\n"
                )
                self.assertEqual(result, 2)
                self.assertIn("stdin cannot be used for both", errors)

    def test_traversal_is_rejected(self):
        for path in ("../outside.py", r"..\outside.py"):
            with self.subTest(path=path):
                result, _, errors = self.run_tool(f"Use {path}\n")
                self.assertEqual(result, 1)
                self.assertIn("invalid or unsafe path", errors)


if __name__ == "__main__":
    unittest.main()
