#!/usr/bin/env python3
"""Self-test the seam-test guard, including both rejected patterns."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-seam-tests.py")


class SeamTestGuardTests(unittest.TestCase):
    def test_good_patterns_and_all_repository_targets_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_target(
                root,
                """
import ast

for node in tree.body:
    if isinstance(node, ast.FunctionDef):
        functions.append(node)

added = Child.__dict__.keys() - Parent.__dict__.keys()
assert not added
""",
            )
            result = self._run(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("通过", result.stdout)

        result = self._run(SCRIPT.parents[2])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recursive_walk_reports_class_method_trap(self) -> None:
        self._assert_rejected(
            """
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        functions.append(node)
""",
            "tree.body",
        )

    def test_dir_difference_reports_override_trap(self) -> None:
        self._assert_rejected(
            "assert not (set(dir(Child)) - set(dir(Parent)))",
            "cls.__dict__",
        )

    def _assert_rejected(self, source: str, hint: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_target(root, source)
            result = self._run(root)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("test_example_split.py:", result.stderr)
            self.assertIn(hint, result.stderr)

    @staticmethod
    def _write_target(root: Path, source: str) -> None:
        target = root / "backend" / "tests" / "unit" / "test_example_split.py"
        target.parent.mkdir(parents=True)
        target.write_text(source, encoding="utf-8")

    @staticmethod
    def _run(root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            check=False,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
