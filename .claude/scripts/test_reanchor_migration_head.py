#!/usr/bin/env python3
"""Black-box tests for the one-command Alembic HEAD repair path."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
REANCHOR = SCRIPT_DIR / "reanchor-migration-head.py"
CHECK = SCRIPT_DIR / "check-migration-fork.py"


class ReanchorCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="reanchor-head-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.versions = self.root / "backend/alembic/versions"
        self.versions.mkdir(parents=True)
        (self.root / "backend/alembic.ini").write_text(
            "[alembic]\nscript_location = %(here)s/alembic\n", encoding="utf-8"
        )
        scripts = self.root / ".claude/scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(REANCHOR, scripts / REANCHOR.name)
        shutil.copy2(CHECK, scripts / CHECK.name)
        self.reanchor = scripts / REANCHOR.name
        self.checker = scripts / CHECK.name
        self.sentinel = self.root / "backend/alembic/HEAD"
        self.sentinel.write_text("a\n", encoding="utf-8")
        self.migration("a", None)
        self.git_env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git_env.pop("GIT_DIR", None)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "self-test@example.invalid")
        self.git("config", "user.name", "self-test")
        self.git("add", "-A")
        self.git("commit", "-qm", "base migration")

    def migration(self, revision: str, parent: str | None) -> None:
        (self.versions / f"{revision}.py").write_text(
            f"revision = {revision!r}\ndown_revision = {parent!r}\n", encoding="utf-8"
        )

    def git(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=self.root,
            env=self.git_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_one_command_rechains_and_repairs_sentinel(self) -> None:
        self.git("checkout", "-qb", "base")
        self.migration("b", "a")
        self.sentinel.write_text("b\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "base advances")
        self.git("checkout", "-qb", "feature", "main")
        self.migration("n", "a")
        self.sentinel.write_text("n\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "feature migration")
        self.git("merge", "--no-commit", "base", expected=1)
        self.sentinel.write_text("n\n", encoding="utf-8")
        self.git("add", "backend/alembic/HEAD")
        self.git("commit", "-qm", "merge base")

        result = self.run_script(self.reanchor, "--base", "base")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("down_revision -> b", result.stdout)
        self.assertEqual((self.versions / "n.py").read_text(encoding="utf-8").splitlines()[1], "down_revision = 'b'")
        self.assertEqual(self.sentinel.read_text(encoding="utf-8"), "n\n")
        verified = self.run_script(self.checker, "--base", "base")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_fork_failure_prints_copyable_single_entry_command(self) -> None:
        self.migration("n", "a")
        self.sentinel.write_text("n\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "branch migration")
        self.git("checkout", "-qb", "base")
        self.migration("b", "a")
        self.sentinel.write_text("b\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "base migration")
        self.git("checkout", "-q", "main")

        result = self.run_script(self.checker, "--base", "base")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "uv run --project backend python .claude/scripts/reanchor-migration-head.py --base base",
            result.stdout,
        )

    def test_sentinel_drift_prints_copyable_single_entry_command(self) -> None:
        self.sentinel.write_text("stale\n", encoding="utf-8")
        result = self.run_script(self.checker, "--base", "main")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "uv run --project backend python .claude/scripts/reanchor-migration-head.py",
            result.stdout,
        )

    def test_both_scripts_expose_passing_self_tests(self) -> None:
        for script in (self.reanchor, self.checker):
            with self.subTest(script=script.name):
                result = self.run_script(script, "--self-test")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("OK" if script == self.reanchor else "PASS", result.stdout + result.stderr)

    def test_deliberate_fork_is_rejected(self) -> None:
        self.migration("b", "a")
        self.migration("c", "a")
        result = self.run_script(self.checker, "--base", "main")
        # This is the intentional violation: the checker must go red, not bless it.
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("would leave 2 alembic heads", result.stdout)


def main() -> int:
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ReanchorCommandTests)
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
