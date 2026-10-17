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
LONG_COPY = "This explanatory sentence should be checked by its important parts"


class TestCopyAssertionGuardTests(unittest.TestCase):
    def test_repository_and_frozen_baseline_pass(self) -> None:
        result = self._run(SCRIPT.parents[2])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("21 个精确历史基线项已冻结", result.stdout)

    def test_new_python_and_javascript_sentences_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "backend/tests/unit/test_copy.py",
                f'assert actual == "{LONG_COPY}"\nself.assertEqual(actual, "{LONG_COPY}")\n',
            )
            self._write(
                root,
                "frontend/src/copy.spec.ts",
                f'expect(actual).toBe("{LONG_COPY}")\nexpect(actual).toEqual(`{LONG_COPY}`)\n'
                f'assert.equal(actual, "{LONG_COPY}")\n"{LONG_COPY}" === actual\n',
            )
            self._write(
                root,
                "backend/tests/unit/test_copy.cjs",
                f'assert.deepStrictEqual(actual, "{LONG_COPY}")\n',
            )

            result = self._run(root)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("backend/tests/unit/test_copy.py:1", result.stderr)
        self.assertIn("backend/tests/unit/test_copy.py:2", result.stderr)
        self.assertIn("frontend/src/copy.spec.ts:1", result.stderr)
        self.assertIn("frontend/src/copy.spec.ts:2", result.stderr)
        self.assertIn("frontend/src/copy.spec.ts:3", result.stderr)
        self.assertIn("frontend/src/copy.spec.ts:4", result.stderr)
        self.assertIn("backend/tests/unit/test_copy.cjs:1", result.stderr)
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

    def test_removed_baseline_assertions_are_reported_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory))

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("基线项已过期或数量减少", result.stderr)

    def test_negated_assertions_and_non_prose_values_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(
                root,
                "backend/tests/unit/test_values.py",
                '''
assert path == "https://example.test/api/a-long-route"
assert identifier == "a_long_identifier_name_that_is_not_copy"
assert response == "429 429 429 429 429 429 429 429 429 429"
assert status == "not found"
assert actual != "This explanatory sentence should be checked by its important parts"
assert "archived/removed notices should preserve the relevant message for readers" in text
''',
            )
            self._write(
                root,
                "frontend/src/values.spec.ts",
                '''
expect(url).toBe('/topics/a-long-route')
expect(code).toBe('a_long_identifier_name_that_is_not_copy')
expect(actual).not.toBe("This explanatory sentence should be checked by its important parts")
''',
            )

            result = self._run(root)

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("test_values.py:", result.stderr)
        self.assertNotIn("values.spec.ts:", result.stderr)
        self.assertIn("基线项已过期或数量减少", result.stderr)

    def test_explicit_root_works_when_scanner_script_is_shallow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "scanner.py"
            script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script), "--root", str(root / "fixture")],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotIn("IndexError", result.stderr)
        self.assertIn("基线项已过期或数量减少", result.stderr)

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
