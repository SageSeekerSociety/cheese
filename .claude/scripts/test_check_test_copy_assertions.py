#!/usr/bin/env python3
"""Self-test the whole-sentence copy assertion guard."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-test-copy-assertions.py")
BASELINE_PATH = "backend/tests/integration/test_domain_error_language.py"
BASELINE_COPY = "The source channel hasn't run yet (there's no session to clone)"


class TestCopyAssertionGuardTests(unittest.TestCase):
    def test_repository_and_frozen_baseline_pass(self) -> None:
        result = self._run(SCRIPT.parents[2])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("20 个历史基线项已冻结", result.stdout)

    def test_new_python_and_javascript_sentences_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "backend/tests/unit/test_copy.py",
                'assert actual == "This explanatory sentence should be checked by its important parts"\n',
            )
            self._write(
                root,
                "frontend/src/copy.spec.ts",
                'expect(actual).toBe("This explanatory sentence should be checked by its important parts")\n',
            )

            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("backend/tests/unit/test_copy.py:1", result.stderr)
        self.assertIn("frontend/src/copy.spec.ts:1", result.stderr)
        self.assertIn("Python 用 `in`", result.stderr)
        self.assertIn("Vitest 用 `toContain`", result.stderr)

    def test_baseline_is_exact_and_does_not_allow_an_extra_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assertion = f'assert actual == "{BASELINE_COPY}"\n'
            self._write(root, BASELINE_PATH, assertion + assertion)

            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"{BASELINE_PATH}:2", result.stderr)
        self.assertIn("按必须表达的片段断言", result.stderr)

    def test_paths_identifiers_status_codes_and_short_values_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "backend/tests/unit/test_values.py",
                '''
assert path == "https://example.test/api/a-long-route"
assert identifier == "a_long_identifier_name_that_is_not_copy"
assert response == "HTTP 429 status code"
assert status == "not found"
''',
            )
            self._write(
                root,
                "frontend/src/values.spec.ts",
                '''
expect(url).toBe('/topics/a-long-route')
expect(code).toBe('a_long_identifier_name_that_is_not_copy')
''',
            )

            result = self._run(root)

        self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def _write(root: Path, relative_path: str, content: str) -> None:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

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
