"""The bisect that finds the commit a batch's failing tests point at.

The rules, as stated before the code was written:

- A batch's commits are searched oldest first. A red midpoint means the
  culprit is at or before it; a green one means after it.
- The search costs ceil(log2 n) steps and never more than the cap. A range the
  cap cannot decide is reported as indeterminate, never as a guess.
- A test file that does not exist on a commit is not a failure there, and a
  runner that collects no test exits 5 rather than failing.
- The answer is the head's `bisect-culprit` check, which is what the landing
  watch reads; without a token the step summary is still written.
"""

import importlib.util
import os
import pathlib
import unittest
from unittest import mock

_SCRIPT = pathlib.Path(__file__).with_name("bisect-culprit.py")
_SPEC = importlib.util.spec_from_file_location("bisect_culprit", _SCRIPT)
bisect_culprit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bisect_culprit)


class BisectTest(unittest.TestCase):
    def test_the_first_red_commit_is_found(self):
        commits = ["a", "b", "c", "d", "e"]
        broken = "d"

        def is_red(commit):
            return commits.index(commit) >= commits.index(broken)

        culprit, steps = bisect_culprit.bisect(commits, is_red)
        self.assertEqual(culprit, broken)
        self.assertLessEqual(steps, bisect_culprit.MAX_STEPS)

    def test_the_head_can_be_the_culprit_and_is_tested(self):
        commits = ["a", "b", "c", "d"]

        def is_red(commit):
            return commit == "d"

        culprit, _steps = bisect_culprit.bisect(commits, is_red)
        self.assertEqual(culprit, "d")

    def test_a_range_that_is_all_green_has_no_culprit(self):
        culprit, _steps = bisect_culprit.bisect(["a", "b", "c"], lambda _commit: False)
        self.assertIsNone(culprit)

    def test_a_range_too_long_for_the_cap_is_indeterminate(self):
        commits = [f"c{i}" for i in range(20)]

        def is_red(commit):
            return commits.index(commit) >= 10

        culprit, steps = bisect_culprit.bisect(commits, is_red)
        self.assertIsNone(culprit)
        self.assertEqual(steps, bisect_culprit.MAX_STEPS)

    def test_the_budget_is_spent_on_the_steps_it_needs(self):
        commits = ["a", "b", "c", "d", "e", "f", "g", "h"]

        def is_red(commit):
            return commits.index(commit) >= commits.index("f")

        culprit, steps = bisect_culprit.bisect(commits, is_red)
        self.assertEqual(culprit, "f")
        self.assertLessEqual(steps, 3)


class SplitTest(unittest.TestCase):
    def test_each_runner_gets_its_own_paths_without_the_top_directory(self):
        backend, frontend = bisect_culprit.split_tests(
            ["backend/tests/unit/test_a.py", "frontend/src/foo.test.ts"]
        )
        self.assertEqual(backend, ["tests/unit/test_a.py"])
        self.assertEqual(frontend, ["src/foo.test.ts"])

    def test_a_path_the_runner_already_relative_keeps_its_shape(self):
        backend, frontend = bisect_culprit.split_tests(
            ["tests/unit/test_a.py", "src/foo.test.ts"]
        )
        self.assertEqual(backend, ["tests/unit/test_a.py"])
        self.assertEqual(frontend, ["src/foo.test.ts"])


class PublishTest(unittest.TestCase):
    def test_without_a_token_the_step_summary_is_still_written(self):
        with mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": "/tmp/x"}, clear=False):
            with mock.patch.dict(
                os.environ, {"GITHUB_TOKEN": "", "GITHUB_REPOSITORY": ""}, clear=False
            ):
                with mock.patch.object(pathlib.Path, "write_text") as write:
                    with mock.patch("builtins.print"):
                        bisect_culprit.publish("head", "abc")
        write.assert_called_once_with("bisect-culprit: abc\n")


if __name__ == "__main__":
    unittest.main()
