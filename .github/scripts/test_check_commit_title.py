"""This repo's commit-title check, run the way CI runs it."""

from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).with_name("check-commit-title.py")


def run(*titles, stdin=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *titles],
        input=stdin,
        capture_output=True,
        text=True,
    )


class CommitTitleTest(unittest.TestCase):
    def test_accepts_this_repos_titles(self):
        for title in [
            "fix(accept): open the PR as the requester, not the bot",
            "feat(api)!: drop the v1 topic endpoints",
            "fix(oauth): GitHub token refresh silently returns None",
            "feat(threads): reply under a channel message in a 支线, where 芝士 answers (#2846)",
            "fix(compute): a room's later sessions start on its whole choice, and every"
            " session on an enrolled device shows the notice (#1899)",
        ]:
            with self.subTest(title=title):
                result = run(title)
                self.assertEqual(result.returncode, 0, result.stdout)

    def test_refuses_titles_outside_the_convention(self):
        for title in [
            "修一下分页的 bug",
            "update stuff",
            "misc: tidy things",
            "WIP: 记忆改造 (#1815)",
            "fix(palette): 归档的话题不再出现在「等你处理」里 (#2656)",
            "fix: stop the crash.",
            "fix: stop the crash. (#12)",
            "",
        ]:
            with self.subTest(title=title):
                result = run(title)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("::error::", result.stdout)

    def test_reads_one_title_per_line_from_stdin(self):
        good = "fix: one (#1)\nfeat: two (#2)\n"
        self.assertEqual(run(stdin=good).returncode, 0)
        result = run(stdin=good + "做完了 (#3)\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("做完了", result.stdout)
        self.assertNotIn("fix: one", result.stdout)

    def test_no_title_at_all_is_a_failure(self):
        self.assertEqual(run(stdin="").returncode, 1)


if __name__ == "__main__":
    unittest.main()
