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

    def test_the_platforms_placeholder_is_named_and_answered(self):
        """The platform's own title reads as that, and comes with the way out:
        set it in the PR title over REST, and the edit checks it again on its
        own, so no new commit is needed."""
        result = run("WIP: 记忆改造")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("still the platform's placeholder title", result.stdout)
        self.assertIn("gh api -X PATCH repos/", result.stdout)
        self.assertIn("pr-title.yml", result.stdout)
        self.assertIn("no new commit", result.stdout)
        # The check also reads a branch's commit subjects (merge queue), where
        # the way out is the message, not the PR title: it says so rather than
        # sending that reader to edit a title nothing reads.
        self.assertIn("amend that commit's message", result.stdout)

    def test_an_ordinary_mistake_is_not_answered_as_a_placeholder(self):
        result = run("update stuff")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("placeholder", result.stdout)

    def test_a_title_that_merely_starts_with_wip_is_not_the_placeholder(self):
        result = run("WIPped up the docs")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn("placeholder", result.stdout)

    def test_wrapping_a_valid_title_in_the_placeholder_does_not_pass_it(self):
        """Recognising the platform's title must not become a way past the
        convention: the same words under `WIP:` are still refused."""
        result = run("WIP: fix(chat): the pane keeps following")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("::error::", result.stdout)

    def test_a_placeholder_among_other_titles_is_answered_too(self):
        result = run(stdin="fix: one (#1)\nWIP: 记忆改造 (#2)\n")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("still the platform's placeholder title", result.stdout)
        self.assertIn("gh api -X PATCH repos/", result.stdout)

    def test_refuses_titles_outside_the_convention(self):
        for title in [
            "修一下分页的 bug",
            "update stuff",
            "misc: tidy things",
            "WIP: 记忆改造 (#1815)",
            "fix(palette): 归档的话题不再出现在「等你处理」里 (#2656)",
            "fix(accept): 修复 GitHub 的 token 刷新失败",
            "fix: 改 PR 描述",
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

    def test_warn_only_reports_the_same_findings_without_failing(self):
        """Post-submit Required CI reads titles that already landed: it reports
        them as warnings and lets the gate through."""
        result = run("--warn-only", stdin="update stuff\nfix: fine\n")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("::warning::", result.stdout)
        self.assertIn("update stuff", result.stdout)
        self.assertNotIn("::error::", result.stdout)
        self.assertIn("already on main", result.stdout)

    def test_warn_only_still_fails_on_nothing_to_check(self):
        # A warnings-only mode is not a silent pass when the input never
        # arrived: an empty check would report nothing about anything. The
        # message is asserted too — without --warn-only being stripped, the
        # flag itself was taken as a title and failed for the wrong reason.
        result = run("--warn-only", stdin="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no title to check", result.stderr)

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
        # Whose title it is decides the answer: the platform's placeholder is
        # not the author's format slip, so it is named and answered as such.
        self.assertIn("still the platform's placeholder title", result.stdout)
        self.assertIn("gh api -X PATCH repos/", result.stdout)
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
