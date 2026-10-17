#!/usr/bin/env python3
"""Self-test the wiring meta-guard, deliberately broken trees included.

Three things are asserted, in the order they matter:

* the tree this repository ships wires every guard it has. That is the finding
  the guard exists for, and pinning it here is what keeps the next unwired
  guard from landing quietly the way #4005, #4010 and #4019 did;
* a guard nothing names is reported, with the sentence that says where to put
  it — naming it in a workflow or in .pre-commit-config.yaml clears it, so does
  a sibling self-test the CI collection runs, naming it in a comment does not,
  and an excuse that is spent or stale is reported rather than honoured;
* the report bites. A copy of the guard with discovery neutered stays green on
  the same tree, so the red the real guard prints is the finding and not the
  fixture — the one thing a tree-shaped assertion cannot tell you by itself.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

GUARD = Path(__file__).with_name("check-guard-wired.py")
REPO = GUARD.parents[2]

def _load_guard():
    spec = importlib.util.spec_from_file_location("check_guard_wired", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: What the guard excuses. Read from the guard rather than copied, so the
#: fixture below carries an excused script for exactly as long as the guard
#: considers it excused.
EXCUSED = tuple(sorted(_load_guard().ON_DEMAND))


def run(root: Path, script: Path = GUARD) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


@contextmanager
def fixture(
    workflow: str | None = "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262\n",
    precommit: str | None = None,
    self_test: str | None = None,
) -> Iterator[Path]:
    """One guard nothing names, plus whatever wiring the caller wants.

    `workflow` is the body of `.github/workflows/repo-guards.yml`; `None` leaves
    the workflow out altogether. `precommit` adds `.pre-commit-config.yaml`
    when it is given, `self_test` a sibling by that file name.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        scripts = root / ".claude/scripts"
        scripts.mkdir(parents=True)
        (scripts / "check-unwired.py").write_text("# a guard\n", encoding="utf-8")
        for name in EXCUSED:
            (scripts / name).write_text("# excused\n", encoding="utf-8")
        if self_test is not None:
            (scripts / self_test).write_text("import unittest\n", encoding="utf-8")
        if workflow is not None:
            path = root / ".github/workflows/repo-guards.yml"
            path.parent.mkdir(parents=True)
            path.write_text(f"jobs:\n  guards:\n    steps:\n{workflow}", encoding="utf-8")
        if precommit is not None:
            (root / ".pre-commit-config.yaml").write_text(precommit, encoding="utf-8")
        yield root


def mutant(script: Path) -> Path:
    """A copy of the guard with `unwired` finding nothing — exempt everything."""
    source = script.read_text(encoding="utf-8")
    broken = source.replace(
        "if script not in ON_DEMAND and not lanes(script, wiring_files, tests)",
        "if False  # mutation: report nothing, whatever the tree looks like",
    )
    if broken == source:
        raise AssertionError("the mutation did not apply: the finding loop moved")
    target = script.with_name("check-guard-wired.mutant.py")
    target.write_text(broken, encoding="utf-8")
    return target


class GuardWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(
            lambda: (GUARD.with_name("check-guard-wired.mutant.py")).unlink(missing_ok=True)
        )

    def test_the_repository_wires_every_guard(self) -> None:
        result = run(REPO)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok:", result.stdout)

    def test_a_guard_nothing_names_is_reported_with_where_to_put_it(self) -> None:
        with fixture() as root:
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(".claude/scripts/check-unwired.py: nothing runs it", result.stderr)
        self.assertIn(".github/workflows/repo-guards.yml", result.stderr)
        self.assertIn(".pre-commit-config.yaml", result.stderr)

    def test_a_step_in_a_workflow_clears_the_finding(self) -> None:
        with fixture(workflow="      - run: python3 .claude/scripts/check-unwired.py\n") as root:
            result = run(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok:", result.stdout)

    def test_a_pre_commit_hook_clears_the_finding(self) -> None:
        hook = "repos:\n  - repo: local\n    hooks:\n      - id: unwired\n        entry: python3 .claude/scripts/check-unwired.py\n"
        with fixture(workflow=None, precommit=hook) as root:
            result = run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_comment_naming_the_guard_is_not_a_wiring(self) -> None:
        with fixture(workflow="      # - run: python3 .claude/scripts/check-unwired.py\n") as root:
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("check-unwired.py: nothing runs it", result.stderr)

    def test_a_trailing_comment_naming_the_guard_is_not_a_wiring(self) -> None:
        with fixture(workflow="      - run: echo hello  # python3 .claude/scripts/check-unwired.py\n") as root:
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_a_sibling_self_test_the_collection_runs_is_a_lane(self) -> None:
        """`test_check_x.py` beside `check-x.py` is started on every push."""
        with fixture(self_test="test_check_unwired.py") as root:
            result = run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_hyphen_named_sibling_is_not_a_lane(self) -> None:
        """The collection globs `test_*.py`, so this file is started by nothing."""
        with fixture(self_test="test-check-unwired.py") as root:
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("check-unwired.py: nothing runs it", result.stderr)

    def test_a_sibling_self_test_does_not_rescue_an_excused_guard(self) -> None:
        """An excused guard has no lane, so its self-test has none either."""
        excused = EXCUSED[0] if EXCUSED else None
        if excused is None:
            self.skipTest("the guard excuses nothing")
        with fixture(
            workflow="      - run: python3 .claude/scripts/check-unwired.py\n",
            self_test=f"test_{excused.replace('-', '_')}",
        ) as root:
            result = run(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ON_DEMAND still excuses", result.stderr)

    def test_a_tree_with_no_wiring_file_at_all_cannot_be_judged(self) -> None:
        with fixture(workflow=None) as root:
            result = run(root)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("cannot judge", result.stderr)

    def test_an_excuse_whose_guard_got_wired_is_reported(self) -> None:
        excused = EXCUSED[0] if EXCUSED else None
        if excused is None:
            self.skipTest("the guard excuses nothing")
        with fixture(workflow=f"      - run: python3 .claude/scripts/{excused}\n") as root:
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"ON_DEMAND still excuses {excused}", result.stderr)

    def test_an_excuse_for_a_script_that_is_gone_is_reported(self) -> None:
        excused = EXCUSED[0] if EXCUSED else None
        if excused is None:
            self.skipTest("the guard excuses nothing")
        with fixture() as root:
            (root / ".claude/scripts" / excused).unlink()
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"ON_DEMAND names {excused}, which is not in .claude/scripts", result.stderr)

    def test_the_report_bites_a_copy_that_reports_nothing_stays_green(self) -> None:
        """With discovery neutered the same tree passes, so the red was the finding."""
        with fixture() as root:
            self.assertEqual(run(root).returncode, 1)
            result = run(root, mutant(GUARD))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
