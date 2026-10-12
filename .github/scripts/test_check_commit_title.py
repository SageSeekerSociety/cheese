"""This repo's commit-title check, run the way CI runs it."""

from pathlib import Path
import json
import subprocess
import sys
import tempfile
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
            "fix(chat): reply in a 支线",
            "fix(changes): say 暂无改动 only when it is true (#3408)",
            "fix(teams): the roster offers 移除成员 only where the viewer may (#3285)",
            "feat(memory): keep 记忆 and 支线 notes under one 芝士 folder",
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
            "fix(accept): 修复 GitHub 的 token 刷新失败",
            "fix: 改 PR 描述",
            "fix: 修复 tests",
            "fix: 更新 Dockerfile",
            "fix(chat): 回复变长时对话栏不再贴住最下端 and more",
            "fix(chat): 回复变长时对话栏不再贴住最下端 (#3423)",
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

    def test_a_draft_pull_requests_placeholder_title_is_not_judged(self):
        draft = pull_request(title="WIP: 回复变长时对话栏不再贴住最下端", draft=True)
        result = run("--pull-request", draft)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("::error::", result.stdout)

    def test_a_pull_request_ready_for_review_is_judged_by_its_title(self):
        ready = pull_request(title="WIP: 回复变长时对话栏不再贴住最下端", draft=False)
        result = run("--pull-request", ready)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("::error::", result.stdout)
        retitled = pull_request(
            title="fix(chat): the pane keeps following when the rows above it shrink",
            draft=False,
        )
        self.assertEqual(run("--pull-request", retitled).returncode, 0)


def pull_request(*, title, draft):
    """A pull request as the GitHub API returns it, written to a file."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    with handle:
        json.dump({"number": 3423, "title": title, "draft": draft}, handle)
    return handle.name


if __name__ == "__main__":
    unittest.main()
