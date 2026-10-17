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
        self.git("add", "src")
        self.git("commit", "-qm", "initial")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, text=True
        ).stdout

    def run_tool(self, brief: str, *options: str):
        brief_file = self.root / "brief.txt"
        brief_file.write_text(brief, encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = verify.main(["--root", str(self.root), "--brief", str(brief_file), *options])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_bad_brief_reports_missing_and_both_in_flight_sets(self):
        # A deliberately invalid brief: one nonexistent path and one path
        # claimed by each in-flight source.
        result, _, errors = self.run_tool(
            "Inspect src/missing.py and src/overlap.py\n",
            "--in-flight-actual", "src/overlap.py",
            "--in-flight-declared", "src/overlap.py",
        )
        self.assertEqual(result, 1)
        self.assertIn("path does not exist", errors)
        self.assertIn("src/missing.py", errors)
        self.assertIn("overlaps in-flight", errors)
        self.assertIn("src/overlap.py", errors)

    def test_compliant_brief_accepts_existing_and_new_paths(self):
        result, output, errors = self.run_tool(
            "Use src/existing.py; 新建 .claude/new-helper.py\n",
            "--in-flight-actual", "src/unrelated.py",
            "--in-flight-declared", "docs/unrelated.md",
        )
        self.assertEqual(result, 0, errors)
        self.assertIn("OK:", output)
        self.assertEqual(errors, "")

    def test_new_marker_for_existing_path_is_red(self):
        result, _, errors = self.run_tool("新建 src/existing.py\n")
        self.assertEqual(result, 1)
        self.assertIn("marked new but already exists", errors)

    def test_traversal_is_rejected(self):
        result, _, errors = self.run_tool("Use ../outside.py\n")
        self.assertEqual(result, 1)
        self.assertIn("invalid or unsafe path", errors)


if __name__ == "__main__":
    unittest.main()
