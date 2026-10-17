#!/usr/bin/env python3
"""Self-test the wiring meta-guard, deliberately broken trees included.

Three things are asserted, in the order they matter:

* the tree this repository ships wires every guard it has. That is the finding
  the guard exists for, and pinning it here is what keeps the next unwired
  guard from landing quietly the way #4005, #4010 and #4019 did;
* a guard nothing names is reported, with the sentence that says where to put
  it — naming it in a workflow, a Taskfile task, .cheese/pre-push or
  .pre-commit-config.yaml clears it, and a .sh guard is discovered like a .py
  one, so does
  a sibling self-test the CI collection runs that names the guard; a comment, a
  hook's `files:` pattern, a sibling that never mentions the guard and the
  hyphen-named sibling the collection does not glob do not, and an excuse that
  is spent (its guard wired or gone) or stale (its guard exits 0) is reported
  rather than honoured;
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

    Each excused guard is a stub that exits non-zero: an excuse only stands
    while its guard is red on the tree, and a fixture whose excused guards
    exited 0 would report every entry as stale.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        scripts = root / ".claude/scripts"
        scripts.mkdir(parents=True)
        (scripts / "check-unwired.py").write_text("# a guard\n", encoding="utf-8")
        for name in EXCUSED:
            body = "exit 1\n" if name.endswith(".sh") else "raise SystemExit(1)\n"
            (scripts / name).write_text(body, encoding="utf-8")
        if self_test is not None:
            (scripts / self_test).write_text(
                'GUARD = "check-unwired.py"\n', encoding="utf-8"
            )
        if workflow is not None:
            path = root / ".github/workflows/repo-guards.yml"
            path.parent.mkdir(parents=True)
            path.write_text(f"jobs:\n  guards:\n    steps:\n{workflow}", encoding="utf-8")
        if precommit is not None:
            (root / ".pre-commit-config.yaml").write_text(precommit, encoding="utf-8")
        yield root


def mutant(script: Path, directory: Path) -> Path:
    """A copy of the guard with `unwired` finding nothing — exempt everything.

    Written into `directory`, never beside the guard: the copy's name matches
    check-*.py, so a leftover in .claude/scripts would be judged by the guard
    itself as an unwired guard, and a self-test that litters the tree it judges
    is one SIGKILL away from a confusing red on the next run.
    """
    source = script.read_text(encoding="utf-8")
    broken = source.replace(
        "if script not in ON_DEMAND and not lanes(script, wiring_files, tests)",
        "if False  # mutation: report nothing, whatever the tree looks like",
    )
    if broken == source:
        raise AssertionError("the mutation did not apply: the finding loop moved")
    target = directory / "check-guard-wired-mutant.py"
    target.write_text(broken, encoding="utf-8")
    return target


class GuardWiringTests(unittest.TestCase):

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

    def test_a_sibling_that_never_names_the_guard_is_not_a_lane(self) -> None:
        """A stub with the right name cannot be starting the guard."""
        with fixture(self_test="test_check_unwired.py") as root:
            (root / ".claude/scripts/test_check_unwired.py").write_text(
                "import unittest\n", encoding="utf-8"
            )
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(".claude/scripts/check-unwired.py: nothing runs it", result.stderr)

    def test_a_shell_guard_nothing_names_is_reported(self) -> None:
        """Guards are .py and .sh; dropping the .sh half of discovery must not stay green."""
        ok = "      - run: python3 .claude/scripts/check-unwired.py\n"
        with fixture(workflow=ok) as root:
            (root / ".claude/scripts/check-shell.sh").write_text(
                "#!/bin/sh\n", encoding="utf-8"
            )
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(".claude/scripts/check-shell.sh: nothing runs it", result.stderr)

    def test_a_taskfile_mention_is_a_wiring(self) -> None:
        with fixture(workflow=None) as root:
            (root / "Taskfile.yml").write_text(
                "tasks:\n  guards:\n    cmds:\n"
                "      - python3 .claude/scripts/check-unwired.py\n",
                encoding="utf-8",
            )
            result = run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_pre_push_hook_mention_is_a_wiring(self) -> None:
        with fixture(workflow=None) as root:
            hook = root / ".cheese/pre-push"
            hook.parent.mkdir()
            hook.write_text("python3 .claude/scripts/check-unwired.py\n", encoding="utf-8")
            result = run(root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_pre_commit_files_pattern_is_not_a_wiring(self) -> None:
        """`files:` decides when a hook runs; it does not start a guard."""
        hook = (
            "repos:\n  - repo: local\n    hooks:\n      - id: other\n"
            "        entry: python3 other.py\n"
            "        files: .claude/scripts/check-unwired.py\n"
        )
        with fixture(workflow=None, precommit=hook) as root:
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(".claude/scripts/check-unwired.py: nothing runs it", result.stderr)

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

    def test_an_excuse_whose_guard_turned_green_is_reported(self) -> None:
        """The entry claims the guard is red; a guard that exits 0 no longer is."""
        excused = EXCUSED[0] if EXCUSED else None
        if excused is None:
            self.skipTest("the guard excuses nothing")
        wired = "      - run: python3 .claude/scripts/check-unwired.py\n"
        with fixture(workflow=wired) as root:
            (root / ".claude/scripts" / excused).write_text(
                "raise SystemExit(0)\n", encoding="utf-8"
            )
            result = run(root)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            f"{excused} is green now: delete the ON_DEMAND entry", result.stderr
        )

    def test_every_excuse_stands_on_a_non_zero_exit(self) -> None:
        """`cannot judge` (exit 2) is not green, and every entry is really run.

        The stub leaves a marker so this fails if the guard stops running the
        entry at all: a run that never happens cannot mis-report exit 2.
        """
        if not EXCUSED:
            self.skipTest("the guard excuses nothing")
        wired = "      - run: python3 .claude/scripts/check-unwired.py\n"
        with fixture(workflow=wired) as root:
            for name in EXCUSED:
                if name.endswith(".sh"):
                    body = (
                        f'printf 2 > "$(dirname "$0")/{name}.ran"\n'
                        "exit 2\n"
                    )
                else:
                    body = (
                        "from pathlib import Path\n"
                        f"Path(__file__).with_name({name + '.ran'!r}).write_text('2')\n"
                        "raise SystemExit(2)\n"
                    )
                (root / ".claude/scripts" / name).write_text(body, encoding="utf-8")
            result = run(root)
            marks = [root / ".claude/scripts" / f"{name}.ran" for name in EXCUSED]
            ran = [mark.exists() for mark in marks]
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(ran, [True] * len(EXCUSED), "an entry was not run")

    def test_an_excuse_is_not_honoured_when_the_guard_cannot_start(self) -> None:
        """A guard that never started gives no verdict either."""
        excused = EXCUSED[0] if EXCUSED else None
        if excused is None:
            self.skipTest("the guard excuses nothing")
        guard = _load_guard()
        wired = "      - run: python3 .claude/scripts/check-unwired.py\n"

        def refuse(*args, **kwargs):
            raise OSError("exec format error")

        with fixture(workflow=wired) as root:
            original = guard.subprocess.run
            guard.subprocess.run = refuse
            try:
                code, problems, _ = guard.judge(root)
            finally:
                guard.subprocess.run = original
        self.assertEqual(code, 1, problems)
        self.assertTrue(
            any("cannot be started" in problem for problem in problems), problems
        )

    def test_a_relative_root_is_resolved_before_the_guard_is_run(self) -> None:
        """`--root repo` must not resolve twice and honour a green excuse."""
        excused = EXCUSED[0] if EXCUSED else None
        if excused is None:
            self.skipTest("the guard excuses nothing")
        wired = "      - run: python3 .claude/scripts/check-unwired.py\n"
        with fixture(workflow=wired) as root:
            (root / ".claude/scripts" / excused).write_text(
                "raise SystemExit(0)\n", encoding="utf-8"
            )
            result = subprocess.run(
                [sys.executable, str(GUARD), "--root", root.name],
                cwd=root.parent,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(
            f"{excused} is green now: delete the ON_DEMAND entry", result.stderr
        )

    def test_an_excuse_that_never_finishes_is_not_honoured(self) -> None:
        """A guard that hangs gives no verdict, so it cannot show the tree is red."""
        excused = EXCUSED[0] if EXCUSED else None
        if excused is None:
            self.skipTest("the guard excuses nothing")
        guard = _load_guard()
        guard.EXCUSE_TIMEOUT = 0.5
        wired = "      - run: python3 .claude/scripts/check-unwired.py\n"
        with fixture(workflow=wired) as root:
            (root / ".claude/scripts" / excused).write_text(
                "import time\ntime.sleep(30)\n", encoding="utf-8"
            )
            code, problems, _ = guard.judge(root)
        self.assertEqual(code, 1, problems)
        self.assertTrue(
            any("did not finish" in problem for problem in problems), problems
        )

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
        with fixture() as root, tempfile.TemporaryDirectory() as scratch:
            self.assertEqual(run(root).returncode, 1)
            result = run(root, mutant(GUARD, Path(scratch)))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
